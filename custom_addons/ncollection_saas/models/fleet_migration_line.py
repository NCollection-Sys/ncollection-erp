# -*- coding: utf-8 -*-
"""Per-tenant target of a fleet migration — the isolated-subprocess engine.

Each line upgrades ONE tenant DB in an isolated ``odoo -u`` subprocess (never a
cross-DB ORM cursor), after a pre-upgrade snapshot, then smoke-probes it. Any
failure is caught HERE (failure isolation): the line is flagged, the tenant is
marked ``error`` in the registry, and — per the migration's ``auto_restore``
flag — its pre-upgrade snapshot is restored in place; the wave continues.
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
        ('upgrading', 'Upgrading'),
        ('probing', 'Probing'),
        ('done', 'Done'),
        ('failed', 'Failed'),
        ('restored', 'Restored'),
        ('skipped', 'Skipped (dry-run)'),
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
        if migration.dry_run:
            self._mark('skipped', self.env._(
                "DRY-RUN: would run `odoo -u %(m)s` on %(db)s.",
                m=','.join(modules), db=db))
            return
        self.write({'started_at': fields.Datetime.now()})
        try:
            self._snapshot()
            self._mark('upgrading', self.env._("Upgrading: %s", ','.join(modules)),
                       stamp=False)
            self._upgrade(db, modules)
            self._mark('probing', self.env._("Smoke probe…"), stamp=False)
            self._probe(db, modules)
            self._mark('done', self.env._("Upgrade OK."))
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

    def _upgrade(self, db, modules):
        cmd = ['odoo'] + self._odoo_conn_args(db) + [
            '-u', ','.join(modules),
            '--stop-after-init', '--no-http', '--max-cron-threads=0']
        self._run_odoo_subprocess(cmd, self.env._("module upgrade"))

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
