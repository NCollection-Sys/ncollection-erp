# -*- coding: utf-8 -*-
"""Per-tenant target of a fleet migration — the isolated-subprocess engine.

Each line acts on ONE tenant DB in an isolated ``odoo`` subprocess (never a
cross-DB ORM cursor), after a pre-change snapshot, then smoke-probes it. Any
failure is caught HERE (failure isolation): the line is flagged, the tenant is
marked ``error`` in the registry, and — per the migration's ``auto_restore``
flag — its pre-change snapshot is restored in place; the wave continues.

**Two operations, and they are NOT interchangeable** (#218). Verified against
``odoo/modules/loading.py``:

    if install_modules:   # -i
        Module.search([('state', '=', 'uninstalled'), ...])
    if upgrade_modules:   # -u
        Module.search([('state', 'in', ('installed', 'to upgrade')), ...])

So ``-u`` acts ONLY on modules the tenant already has — it cannot add one, and
on a tenant lacking the module it is a silent no-op. ``-i`` acts ONLY on
uninstalled ones, which makes it inherently idempotent: re-running cannot
reinstall or disturb a tenant that already has the module.

That asymmetry is why #218 (backfill ``ncollection_auth`` onto tenants
provisioned before #178) could not be done with the upgrade path, and why the
install path needs no "already installed?" bookkeeping of its own.
"""
import logging

from odoo import fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

_TERMINAL_STATES = ('done', 'failed', 'restored', 'skipped')
_TODO_ACT = 'mail.mail_activity_data_todo'


class FleetMigrationLine(models.Model):
    _name = 'ncollection.fleet.migration.line'
    _description = 'Tenant Fleet Migration Target'
    _inherit = ['ncollection.saas.subprocess.mixin']
    _order = 'wave, id'

    migration_id = fields.Many2one(
        'ncollection.fleet.migration', required=True, ondelete='cascade', index=True)
    tenant_id = fields.Many2one(
        'ncollection.tenant', required=True, ondelete='cascade')
    database_name = fields.Char(related='tenant_id.database_name', store=True)
    wave = fields.Integer(default=0, help="0 = canary; 1..N = rolling waves.")
    state = fields.Selection([
        ('pending', 'Pending'),
        ('snapshotting', 'Snapshotting'),
        # Key kept as 'upgrading' so no data migration is needed; the LABEL is
        # operation-neutral because this state now also covers an install (#218).
        ('upgrading', 'Applying'),
        ('probing', 'Probing'),
        ('done', 'Done'),
        ('failed', 'Failed'),
        ('restored', 'Restored'),
        # Now two causes: a dry run, or an install whose modules are all present.
        ('skipped', 'Skipped'),
    ], default='pending', required=True)
    backup_id = fields.Many2one(
        'ncollection.backup', readonly=True,
        help="Pre-upgrade snapshot for rollback.")
    message = fields.Text(readonly=True)
    started_at = fields.Datetime(readonly=True)
    finished_at = fields.Datetime(readonly=True)

    # ---- the per-tenant engine (queue_job-callable) ----------------------

    def run_line(self):
        """Upgrade this one tenant. Public so queue_job can call it in a worker.
        Never raises past the record — failure isolation keeps the wave going."""
        self.ensure_one()
        if self.state != 'pending':
            return
        migration = self.migration_id
        if migration.state == 'cancelled':
            self._mark('skipped', self.env._(
                "Migration cancelled before this tenant ran."))
            return
        db = self.database_name
        try:
            self._assert_safe_db_name(db)          # injection / self-target guard
            modules = migration._module_list()
        except Exception as exc:                    # noqa: BLE001
            # Refused before any DB work — the tenant DB is untouched, so it is
            # NOT flagged 'error'.
            self._mark('failed', self.env._("Refused: %s", exc))
            return
        install = migration.operation == 'install'
        if migration.dry_run:
            # Names the flag it would actually run. Saying "-u" on an install
            # dry-run would misreport the one thing the operator is checking.
            self._mark('skipped', self.env._(
                "DRY-RUN: would run `odoo %(flag)s %(m)s` on %(db)s.",
                flag='-i' if install else '-u', m=','.join(modules), db=db))
            return
        self.write({'started_at': fields.Datetime.now()})
        try:
            # Install only: skip tenants that already have every module, BEFORE
            # snapshotting. On a backfill most of the fleet is already fine, and
            # a full backup each is a lot of disk and time for a no-op.
            if install and self._already_satisfied(db, modules):
                self._mark('skipped', self.env._(
                    "Already installed: %s. Nothing to do.", ','.join(modules)))
                return
            self._snapshot()
            self._mark('upgrading',
                       self.env._("%(label)s: %(m)s",
                                  label=self._operation_label().capitalize(),
                                  m=','.join(modules)),
                       stamp=False)
            self._apply(db, modules)
            self._mark('probing', self.env._("Smoke probe…"), stamp=False)
            self._probe(db, modules)
            self._mark('done', self.env._("%s OK.", self._operation_label()))
        except Exception as exc:                    # noqa: BLE001 — isolate the failure
            self._safe_handle_failure(db, exc)

    def _safe_handle_failure(self, db, exc):
        """run_line promises never to raise past the record; guard the failure
        bookkeeping itself so a transient write error can't abort the whole wave
        (sync) or hang it forever on a non-terminal line (async)."""
        try:
            self._handle_failure(db, exc)
        except Exception:                           # noqa: BLE001
            _logger.exception("Fleet failure-bookkeeping error for %s", db)
            try:
                self.write({'state': 'failed',
                            'message': 'FAILED; bookkeeping error (see server log).'})
            except Exception:                       # noqa: BLE001
                _logger.exception("Fleet: could not mark line failed for %s", db)

    def _snapshot(self):
        self._mark('snapshotting', self.env._("Pre-upgrade snapshot…"), stamp=False)
        backup = self.env['ncollection.backup'].create({
            'tenant_id': self.tenant_id.id, 'backup_type': 'daily'})
        backup.run_backup()
        if backup.status != 'done':
            raise UserError(self.env._(
                "Pre-upgrade snapshot failed: %s", backup.error_log or '?'))
        self.backup_id = backup.id

    def _apply(self, db, modules):
        """Run the migration's operation against this tenant.

        ``-i`` and ``-u`` are the ONLY difference between install and upgrade;
        the snapshot, probe, failure isolation and restore paths are shared, so
        there is no second engine to keep in step.
        """
        flag = '-i' if self.migration_id.operation == 'install' else '-u'
        cmd = ['odoo'] + self._odoo_conn_args(db) + [
            flag, ','.join(modules),
            '--stop-after-init', '--no-http', '--max-cron-threads=0']
        self._run_odoo_subprocess(cmd, self._operation_label())

    def _operation_label(self):
        return (self.env._("module install")
                if self.migration_id.operation == 'install'
                else self.env._("module upgrade"))

    def _already_satisfied(self, db, modules):
        """True when every requested module is ALREADY installed here.

        Only meaningful for an install run. ``-i`` would be a harmless no-op
        anyway (it selects only ``uninstalled`` modules), but reaching it means
        first taking a FULL BACKUP of a tenant that needs nothing — on a
        backfill, that is most of the fleet. Checking first turns those into a
        cheap skip.

        Reported as SKIPPED, never as failed: 'this tenant already has it' is
        the expected outcome for most of a backfill, and a run that scores it as
        a failure trains the operator to ignore the summary (#221).
        """
        script = (
            "mods = env['ir.module.module'].search([('name', 'in', %r)])\n"
            "have = mods.filtered(lambda m: m.state == 'installed').mapped('name')\n"
            "print('NC_HAVE=%%s' %% ','.join(sorted(have)))\n" % (list(modules),)
        )
        cmd = ['odoo', 'shell'] + self._odoo_conn_args(db) + ['--log-level=error']
        out = self._run_odoo_subprocess(
            cmd, self.env._("module state check"), stdin=script)
        for line in reversed((out or '').splitlines()):
            if line.strip().startswith('NC_HAVE='):
                have = {m for m in line.strip()[len('NC_HAVE='):].split(',') if m}
                return set(modules).issubset(have)
        # No marker: do NOT assume "already installed" — that would silently skip
        # a tenant the backfill exists to fix. Fall through and let the install
        # run; -i is idempotent, so the cost of being wrong this way is nil.
        return False

    def _probe(self, db, modules):
        """Smoke probe: the upgraded modules are actually ``installed`` AND a
        basic ORM read succeeds, in an isolated odoo shell — proves the upgrade
        applied (not stuck 'to upgrade') and left the registry usable."""
        script = (
            "mods = env['ir.module.module'].search([('name', 'in', %r)])\n"
            "bad = mods.filtered(lambda m: m.state != 'installed').mapped('name')\n"
            "ok = (not bad) and env['res.users'].search_count([]) >= 0\n"
            "print('NC_SMOKE_OK=%%s' %% ok)\n" % (list(modules),)
        )
        cmd = ['odoo', 'shell'] + self._odoo_conn_args(db) + ['--log-level=error']
        out = self._run_odoo_subprocess(
            cmd, self.env._("smoke probe"), stdin=script)
        if 'NC_SMOKE_OK=True' not in out:
            raise UserError(self.env._(
                "Smoke probe failed on %s (a module is not 'installed', or the "
                "ORM read failed).", db))

    def _handle_failure(self, db, exc):
        detail = str(exc)[-1500:]
        migration = self.migration_id
        if migration.auto_restore and self.backup_id:
            try:
                self._restore(db)
                self._mark('restored', self.env._(
                    "FAILED, auto-restored from snapshot. %s", detail))
                return  # restored to its pre-upgrade state → still healthy
            except Exception as restore_exc:        # noqa: BLE001
                # warning (not exception): this is a handled, isolated failure —
                # the detail is on the line message + activity; a bare traceback
                # here would also trip CI's traceback gate.
                _logger.warning("Fleet restore failed for %s: %s", db, restore_exc)
                self._flag_tenant_error()
                self._mark('failed', self.env._(
                    "FAILED, and RESTORE FAILED — the database may be gone, manual "
                    "recovery needed. upgrade: %(u)s / restore: %(r)s",
                    u=detail, r=restore_exc))
                migration.activity_schedule(
                    _TODO_ACT,
                    summary=self.env._(
                        "Fleet migration: tenant %s needs manual recovery", db),
                    note=self.env._(
                        "Upgrade AND auto-restore both failed for %(db)s; restore "
                        "its pre-upgrade snapshot manually "
                        "(see RUNBOOK_FLEET_MIGRATION).", db=db))
                return
        self._flag_tenant_error()
        backup_ref = self.backup_id.name if self.backup_id else '-'
        self._mark('failed', self.env._(
            "FAILED (snapshot %(b)s kept for manual restore). %(d)s",
            b=backup_ref, d=detail))

    def _flag_tenant_error(self):
        """A tenant whose fleet upgrade failed is no longer trustworthy as
        'ready' — flag it so config-sync / backup / checkout stop treating it as
        live (mirrors provisioning_job on failure). 'error' is not in the
        engine-only status guard (tenant.py), so no sudo is needed."""
        self.tenant_id.write({'database_status': 'error'})

    def _restore(self, db):
        """In-place rollback: drop the broken DB and restore its pre-upgrade
        snapshot into the same name (ARCHITECTURE §7.4). The FORCE drop first
        terminates any lingering connections the restore script's plain `dropdb`
        could otherwise race on (the script also drops `--if-exists`)."""
        self._assert_safe_db_name(db)
        self._drop_database(db)
        self.backup_id.restore_to(db)

    def _mark(self, state, message, stamp=True):
        vals = {'state': state, 'message': message}
        if stamp and state in _TERMINAL_STATES:
            vals['finished_at'] = fields.Datetime.now()
        self.write(vals)
        # Audit: always append to the run log; surface a failure to the chatter.
        self.migration_id._log(
            "[%s] %s" % (self.database_name or '?', message),
            post=(state == 'failed'))
