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
import os

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
            # Distinct whole strings per operation rather than capitalising a
            # translated fragment: .capitalize() on translated output is locale-
            # blind, and it silently changed the UPGRADE path's wording, which
            # the manifest promises is unchanged. This keeps upgrade byte-identical.
            self._mark('upgrading',
                       self.env._("Installing: %s", ','.join(modules)) if install
                       else self.env._("Upgrading: %s", ','.join(modules)),
                       stamp=False)
            self._apply(db, modules)
            self._mark('probing', self.env._("Smoke probe…"), stamp=False)
            self._probe(db, modules)
            self._mark('done', self.env._("Install OK.") if install
                       else self.env._("Upgrade OK."))
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
        """Step name for the subprocess failure message ("Step X failed").

        Lower-case on purpose: _run_odoo_subprocess embeds it mid-sentence.
        The line's own status messages use whole translatable strings instead
        of transforming this one.
        """
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

        **Never raises.** This is a READ. If the probe itself fails — a timeout,
        a busy server, a transient connection blip — letting that propagate would
        reach ``_handle_failure``, which finds no ``backup_id`` (nothing has been
        snapshotted yet) and so calls ``_flag_tenant_error()``, marking a tenant
        that NOTHING TOUCHED as ``error`` and taking it out of config-sync,
        backup and checkout. A failed read must not do that. We return False
        instead and let the install proceed: if the tenant really is unreachable
        the install fails too, and THAT failure legitimately flags it, because by
        then a write was attempted.
        """
        script = (
            "mods = env['ir.module.module'].search([('name', 'in', %r)])\n"
            "have = mods.filtered(lambda m: m.state == 'installed').mapped('name')\n"
            "print('NC_HAVE=%%s' %% ','.join(sorted(have)))\n" % (list(modules),)
        )
        cmd = ['odoo', 'shell'] + self._odoo_conn_args(db) + ['--log-level=error']
        try:
            out = self._run_odoo_subprocess(
                cmd, self.env._("module state check"), stdin=script)
        except Exception as exc:                    # noqa: BLE001 - a read, see above
            _logger.warning(
                "Fleet: module state check failed for %s (%s); proceeding with "
                "the install, which is idempotent.", db, exc)
            return False
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

    # ---- manual recovery (#244) ------------------------------------------

    def action_restore_in_place(self):
        """Roll ONE failed tenant back to its pre-upgrade snapshot, from the UI.

        With ``auto_restore=OFF`` — the recommended setting for a first real
        fleet migration — a failed tenant is flagged ``error`` and its snapshot
        kept, but recovery was a shell command (``tenant_restore.sh``). The
        backup Restore wizard cannot stand in: it refuses live tenant names by
        design (scratch-only). So the line's own message promised a "manual
        restore" the UI offered no way to perform (#244).

        Same engine as auto-restore — ``_restore`` — so there is no second
        recovery path to keep in step.
        """
        self.ensure_one()
        self._restore_assert_allowed()
        # Serialise: this is a read-check-write gating a destructive
        # drop+restore, the same shape fleet_migration._advance already locks
        # for. Without it two admins — or one double-click across two tabs —
        # both read state='failed' before either writes 'restored', and BOTH
        # run drop+restore against the same live database. The button's
        # confirm= dialog only guards a single tab.
        self.env.cr.execute(
            "SELECT id FROM ncollection_fleet_migration_line WHERE id = %s "
            "FOR UPDATE", (self.id,))
        self.invalidate_recordset(['state'])   # re-read under the lock
        db = self.database_name
        if self.state != 'failed':
            raise UserError(self.env._(
                "Only a FAILED line can be restored; this one is '%s'. Restoring "
                "a tenant that upgraded successfully would roll back a good "
                "upgrade.", self.state))
        if not self.backup_id:
            raise UserError(self.env._(
                "No pre-upgrade snapshot on this line — nothing to restore from. "
                "This line failed before the snapshot was taken, which also means "
                "its database was never modified."))
        self._assert_backup_belongs_here()
        self._assert_backup_restorable()

        try:
            self._restore(db)
        except Exception as exc:                # noqa: BLE001 - see below
            # _drop_database commits on a SEPARATE autocommit psycopg2
            # connection, outside the ORM transaction. So if restore_to then
            # fails, the ORM rolls this method's writes back but THE DATABASE IS
            # ALREADY GONE. Letting the exception escape would leave the line on
            # its stale pre-restore message, finished_at unstamped, and nothing
            # anywhere pointing an operator at a destroyed tenant — the failure
            # is a toast someone has to be watching for, or an RPC error nobody
            # reads. The auto-restore path already handles exactly this
            # (_handle_failure); the manual one must too.
            self._flag_tenant_error()
            self._mark('failed', self.env._(
                "RESTORE FAILED after the database was dropped — '%(db)s' may no "
                "longer exist. Manual recovery needed (off-box backup or PITR). "
                "Attempted by %(user)s. %(err)s",
                db=db, user=self.env.user.name, err=str(exc)[-1500:]))
            self.migration_id.activity_schedule(
                _TODO_ACT,
                summary=self.env._(
                    "Fleet migration: tenant %s needs manual recovery", db),
                note=self.env._(
                    "A manual restore-in-place of %(db)s dropped the database "
                    "and then FAILED to restore it. Recover from off-box backup "
                    "or PITR (see RUNBOOK_FLEET_MIGRATION).", db=db))
            # Deliberately NOT re-raised. Raising rolls the ORM transaction
            # back, which would discard everything just written — the tenant
            # flag, the line message AND the to-do — leaving an error toast as
            # the only trace of a destroyed database. _handle_failure does not
            # re-raise for exactly this reason. Instead return a sticky warning
            # so the operator gets a loud signal AND the record survives.
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': self.env._("Restore FAILED"),
                    'message': self.env._(
                        "The database '%(db)s' was dropped and the restore then "
                        "failed. It may no longer exist — recover from off-box "
                        "backup or PITR. A to-do has been scheduled.", db=db),
                    'type': 'danger',
                    'sticky': True,
                },
            }

        # 'ready' is gated by the engine-only status guard (tenant.py, #228),
        # which authorises on env.su and deliberately NOT on a context key.
        # This IS the engine performing a rollback it owns, so sudo() is the
        # sanctioned path — the same one provisioning uses.
        self.tenant_id.sudo().write({'database_status': 'ready'})
        self._mark('restored', self.env._(
            "Restored in place from snapshot %(b)s by %(user)s (manual).",
            b=self.backup_id.name or '-', user=self.env.user.name))
        return True

    def _assert_backup_belongs_here(self):
        """The snapshot must be THIS tenant's.

        ``tenant_id``, ``state`` and ``backup_id`` are three independently
        writable fields with nothing binding them together, so a line can be
        assembled — or edited — to point a healthy tenant's database at another
        tenant's snapshot. Restoring across tenants would destroy live data AND
        serve one tenant's data under another's database name.

        This does not close the underlying hole: ``ncollection.backup.restore_to``
        is a PUBLIC method and ``base.group_system`` has full write on that
        model, so the same cross-tenant restore was reachable over RPC without
        this button at all.

        CLOSED by #275: ``restore_to`` now runs ``_assert_restore_target``, so
        a live tenant database can only be restored over by its own backup, and
        ``ncollection.backup.write`` refuses to repoint ``tenant_id`` (which
        would otherwise have let a caller manufacture that ownership). This
        check stays because it is still the RIGHT check at this call site, and
        because it compares against the LINE's tenant -- a second, independent
        source of truth from the one the model-level guard reads.
        """
        backup_tenant = self.backup_id.tenant_id
        if backup_tenant and backup_tenant != self.tenant_id:
            raise UserError(self.env._(
                "Snapshot %(b)s belongs to tenant '%(other)s', not '%(this)s'. "
                "REFUSING: restoring it here would destroy this tenant's data "
                "and serve another tenant's data under this database name.",
                b=self.backup_id.name or '-',
                other=backup_tenant.database_name or backup_tenant.display_name,
                this=self.database_name))

    def _restore_assert_allowed(self):
        """Mirror the button's ``groups=`` at the ORM (Rule 4).

        A ``groups=`` attribute hides a button; it does not stop an RPC call.
        This one DROPS A LIVE TENANT DATABASE, so the check has to exist where
        the work happens.

        ``base.group_system`` is not a fresh judgement call — it is what every
        other destructive operation in this layer already gates on (backup,
        restore, fleet migration; see ``security/ir.model.access.csv``). #245
        tracks whether that whole layer should move to
        ``group_platform_admin``; deciding it here would fork the convention
        while that ticket is open.
        """
        if not self.env.user.has_group('base.group_system'):
            raise UserError(self.env._(
                "Restoring a tenant in place requires Settings administrator "
                "rights."))

    def _assert_backup_restorable(self):
        """Refuse if the snapshot file is not actually on disk.

        ``_restore`` DROPS the database before calling ``restore_to``, and
        ``restore_to`` only checks that the ``file_path`` FIELD is set — not
        that the file exists. Without this, one click destroys a live tenant and
        discovers the backup is missing afterwards, leaving nothing to recover
        from.

        Deliberately here and NOT inside ``_restore``: auto-restore runs seconds
        after its own snapshot, so the file is all but certain to be there, and
        adding a check to that shipped path risks breaking automatic recovery
        for no real gain (Rule 4). This button may be pressed days later, after
        retention has swept the file — a completely different risk profile.
        ``backup.py`` uses ``os.path.isfile`` on this same field from this same
        process — but only to decide whether to attempt ``os.remove`` during
        retention cleanup, which is idempotent and harmless if wrong. Gating an
        irreversible DROP is a different risk profile, so existence alone is not
        enough: check size and readability too.

        - **zero bytes** — a truncated write, a disk that filled mid-backup, or a
          partial copy from off-box recovery all leave a real file that
          ``isfile`` happily accepts.
        - **unreadable** — ``isfile``/``stat`` need only search permission on the
          parent directories, not read permission on the file, so a snapshot
          owned by another user passes while ``pg_restore`` cannot open it.

        ``tenant_restore.sh`` does independently verify the result (it counts
        ``information_schema.tables`` and exits 1 on an empty database), but that
        check happens AFTER the drop. Catching it here is what keeps the tenant.
        """
        path = self.backup_id.file_path
        problem = None
        if not path:
            problem = self.env._("no path recorded")
        elif not os.path.isfile(path):
            problem = self.env._("file does not exist")
        elif os.path.getsize(path) == 0:
            problem = self.env._("file is empty (0 bytes)")
        elif not os.access(path, os.R_OK):
            problem = self.env._("file is not readable by this process")
        if problem:
            raise UserError(self.env._(
                "Snapshot for %(b)s is unusable — %(why)s (%(p)s). REFUSING to "
                "restore: the restore drops the live database first, so "
                "proceeding would destroy '%(db)s' with nothing to put back. "
                "Recover the file from off-box backup, or use PITR.",
                b=self.backup_id.name or '-', why=problem,
                p=path or '-', db=self.database_name))

    def _mark(self, state, message, stamp=True):
        vals = {'state': state, 'message': message}
        if stamp and state in _TERMINAL_STATES:
            vals['finished_at'] = fields.Datetime.now()
        self.write(vals)
        # Audit: always append to the run log; surface a failure to the chatter.
        self.migration_id._log(
            "[%s] %s" % (self.database_name or '?', message),
            post=(state == 'failed'))
