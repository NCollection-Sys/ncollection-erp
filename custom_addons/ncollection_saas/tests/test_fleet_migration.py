# -*- coding: utf-8 -*-
"""P3-T14: the fleet migration orchestrator — canary gate, rolling waves,
failure isolation (+ optional auto-restore), dry-run, and the audit log.

The per-tenant `odoo -u` subprocess, the pg_dump snapshot, and the drop/restore
are stubbed so the ORCHESTRATION logic is exercised deterministically without a
real tenant database.
"""
import contextlib
from unittest.mock import patch

from odoo.addons.ncollection_saas.models.backup import NcollectionBackup
from odoo.addons.ncollection_saas.models.saas_subprocess import SaasSubprocessMixin
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestFleetMigration(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Tenant = cls.env['ncollection.tenant']

        def mk(db):
            return Tenant.create({
                'company_name': db, 'database_name': db,
                'database_status': 'ready'})

        cls.canary = mk('fleetcanary')
        cls.t1 = mk('fleetone')
        cls.t2 = mk('fleettwo')
        cls.t3 = mk('fleetthree')

    # ---- helpers ---------------------------------------------------------

    def _migration(self, **kw):
        vals = {
            'name': 'Test migration',
            'module_names': 'ncollection_core',
            'wave_size': 2,
            'canary_tenant_ids': [(6, 0, [self.canary.id])],
        }
        vals.update(kw)
        return self.env['ncollection.fleet.migration'].create(vals)

    def _line(self, migration, tenant):
        return migration.line_ids.filtered(lambda line: line.tenant_id == tenant)

    @contextlib.contextmanager
    def _mocked(self, fail_dbs=(), run_calls=None, restore_calls=None, drop_calls=None):
        """Stub every external side effect: the odoo subprocess, the snapshot,
        and the drop/restore. `fail_dbs` makes the `-u` step raise for those DBs."""
        def _run(_self, cmd, label, stdin=None, env=None, timeout=None):
            db = cmd[cmd.index('-d') + 1] if '-d' in cmd else '?'
            if run_calls is not None:
                run_calls.append((db, 'shell' if 'shell' in cmd else 'upgrade'))
            if 'shell' in cmd:
                return 'NC_SMOKE_OK=True\n'
            if '-u' in cmd and db in fail_dbs:
                raise UserError(_self.env._("simulated upgrade failure on %s", db))
            return ''

        def _run_backup(_self):
            _self.write({
                'status': 'done',
                'file_path': '/tmp/%s.enc' % (_self.database_name or 'x')})

        def _restore_to(_self, target_db):
            if restore_calls is not None:
                restore_calls.append(target_db)
            return target_db

        def _drop(_self, db):
            if drop_calls is not None:
                drop_calls.append(db)

        with patch.object(SaasSubprocessMixin, '_run_odoo_subprocess', _run), \
                patch.object(NcollectionBackup, 'run_backup', _run_backup), \
                patch.object(NcollectionBackup, 'restore_to', _restore_to), \
                patch.object(SaasSubprocessMixin, '_drop_database', _drop):
            yield

    # ---- tests -----------------------------------------------------------

    def test_happy_path_upgrades_every_tenant(self):
        migration = self._migration()
        with self._mocked():
            self.assertTrue(migration.action_run_sync())
        self.assertEqual(migration.state, 'done')
        for tenant in (self.canary, self.t1, self.t2, self.t3):
            self.assertEqual(self._line(migration, tenant).state, 'done')
        self.assertTrue(migration.log)  # audit log populated

    def test_canary_is_upgraded_before_the_fleet(self):
        # canary is wave 0; the rest are waves >= 1.
        migration = self._migration()
        migration._prepare()
        self.assertEqual(self._line(migration, self.canary).wave, 0)
        for tenant in (self.t1, self.t2, self.t3):
            self.assertGreaterEqual(self._line(migration, tenant).wave, 1)

    def test_canary_failure_halts_rollout(self):
        migration = self._migration()  # auto_restore default True
        with self._mocked(fail_dbs={self.canary.database_name}):
            self.assertFalse(migration.action_run_sync())
        self.assertEqual(migration.state, 'canary_failed')
        self.assertIn(self._line(migration, self.canary).state, ('failed', 'restored'))
        # the fleet was never touched
        for tenant in (self.t1, self.t2, self.t3):
            self.assertEqual(self._line(migration, tenant).state, 'pending')

    def test_failure_isolation_with_auto_restore(self):
        migration = self._migration()
        restores, drops = [], []
        with self._mocked(fail_dbs={self.t2.database_name},
                          restore_calls=restores, drop_calls=drops):
            migration.action_run_sync()
        # the run completes past the failed tenant
        self.assertEqual(migration.state, 'done')
        self.assertEqual(self._line(migration, self.t2).state, 'restored')
        self.assertIn(self.t2.database_name, drops)     # dropped
        self.assertIn(self.t2.database_name, restores)  # then restored from snapshot
        # its neighbours upgraded fine
        self.assertEqual(self._line(migration, self.t1).state, 'done')
        self.assertEqual(self._line(migration, self.t3).state, 'done')

    def test_failure_without_auto_restore_flags_and_keeps_snapshot(self):
        migration = self._migration(auto_restore=False)
        restores = []
        with self._mocked(fail_dbs={self.t2.database_name}, restore_calls=restores):
            migration.action_run_sync()
        self.assertEqual(migration.state, 'done')
        line = self._line(migration, self.t2)
        self.assertEqual(line.state, 'failed')
        self.assertEqual(restores, [])          # nothing was restored automatically
        self.assertTrue(line.backup_id)         # snapshot kept for a manual restore

    def test_dry_run_touches_no_database(self):
        migration = self._migration(dry_run=True)
        run_calls = []
        with self._mocked(run_calls=run_calls):
            migration.action_run_sync()
        self.assertEqual(migration.state, 'done')
        self.assertEqual(run_calls, [])          # no subprocess ran
        for tenant in (self.canary, self.t1, self.t2, self.t3):
            line = self._line(migration, tenant)
            self.assertEqual(line.state, 'skipped')
            self.assertFalse(line.backup_id)     # no snapshot taken
        self.assertTrue(migration.log)

    def test_advance_promotes_to_rolling_after_canary_passes(self):
        # the async gate: once the canary wave is done, _advance opens wave 1.
        migration = self._migration(wave_size=2)
        migration._prepare()
        migration.write({'state': 'canary', 'current_wave': 0})
        with self._mocked():
            for line in migration._wave_lines(0):
                line.run_line()
        enqueued = []
        with patch.object(type(migration), '_enqueue_wave',
                          lambda self, wave: enqueued.append(wave)):
            migration._advance()
        self.assertEqual(migration.state, 'rolling')
        self.assertEqual(migration.current_wave, 1)
        self.assertEqual(enqueued, [1])

    def test_db_name_guard_rejects_unsafe_targets(self):
        line = self.env['ncollection.fleet.migration.line']
        with self.assertRaises(ValidationError):
            line._assert_safe_db_name('bad; DROP DATABASE x')
        with self.assertRaises(ValidationError):
            line._assert_safe_db_name(self.env.cr.dbname)  # the platform DB itself

    def test_invalid_module_name_is_rejected(self):
        migration = self._migration(module_names='not a module!')
        with self.assertRaises(ValidationError):
            migration.action_run_sync()

    def test_requires_a_canary_tenant(self):
        migration = self._migration(canary_tenant_ids=[(5, 0, 0)])
        with self.assertRaises(ValidationError):
            migration.action_run_sync()

    def test_acl_only_system_admin_may_run(self):
        user = self.env['res.users'].create({
            'login': 'nc_fleet_nonadmin', 'name': 'Non Admin',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        with self.assertRaises(AccessError):
            self.env['ncollection.fleet.migration'].with_user(user).create({
                'name': 'x', 'module_names': 'ncollection_core'})
