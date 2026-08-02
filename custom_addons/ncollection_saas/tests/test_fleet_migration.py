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
from odoo.addons.ncollection_saas.models.saas_subprocess import (
    DB_NAME_RE,
    RESERVED_DB_NAMES,
    SaasSubprocessMixin,
)
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
    def _mocked(
        self, fail_dbs=(), run_calls=None, restore_calls=None,
        drop_calls=None, restore_fail=False,
    ):
        """Stub every external side effect: the odoo subprocess, the snapshot,
        and the drop/restore. `fail_dbs` makes the `-u` step raise for those DBs;
        `restore_fail` makes the restore raise (worst-case path)."""
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
            if restore_fail:
                raise RuntimeError("simulated restore failure on %s" % target_db)
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
        # the run completes past the failed tenant (restored, not failed)
        self.assertEqual(migration.state, 'done')
        self.assertEqual(self._line(migration, self.t2).state, 'restored')
        self.assertIn(self.t2.database_name, drops)     # dropped
        self.assertIn(self.t2.database_name, restores)  # then restored from snapshot
        # restored to its pre-upgrade state → the tenant stays healthy
        self.assertEqual(self.t2.database_status, 'ready')
        # its neighbours upgraded fine
        self.assertEqual(self._line(migration, self.t1).state, 'done')
        self.assertEqual(self._line(migration, self.t3).state, 'done')

    def test_failure_without_auto_restore_flags_tenant_error(self):
        migration = self._migration(auto_restore=False)
        restores = []
        with self._mocked(fail_dbs={self.t2.database_name}, restore_calls=restores):
            migration.action_run_sync()
        # a real per-tenant failure makes the whole run's headline 'failed'
        self.assertEqual(migration.state, 'failed')
        line = self._line(migration, self.t2)
        self.assertEqual(line.state, 'failed')
        self.assertEqual(restores, [])          # nothing was restored automatically
        self.assertTrue(line.backup_id)         # snapshot kept for a manual restore
        # the tenant is flagged so config-sync/backup/checkout stop trusting it
        self.assertEqual(self.t2.database_status, 'error')
        # the neighbours still succeeded
        self.assertEqual(self._line(migration, self.t1).state, 'done')

    def test_restore_failure_flags_error_and_alerts(self):
        # worst case: upgrade fails, auto-restore is ON, but the restore ALSO
        # fails -> the DB may be gone; flag 'error' + raise an activity.
        migration = self._migration()
        before = len(migration.activity_ids)
        with self._mocked(fail_dbs={self.t2.database_name}, restore_fail=True):
            migration.action_run_sync()
        self.assertEqual(self._line(migration, self.t2).state, 'failed')
        self.assertEqual(self.t2.database_status, 'error')
        self.assertGreater(len(migration.activity_ids), before)  # manual-recovery todo
        # the run still finished past it
        self.assertEqual(migration.state, 'failed')
        self.assertEqual(self._line(migration, self.t3).state, 'done')

    def test_failure_surfaces_to_chatter(self):
        migration = self._migration(auto_restore=False)
        before = len(migration.message_ids)
        with self._mocked(fail_dbs={self.t2.database_name}):
            migration.action_run_sync()
        self.assertGreater(len(migration.message_ids), before)

    def test_cancel_skips_not_yet_run_tenants(self):
        # Cancel must stop tenants that a worker picks up after cancellation.
        migration = self._migration()
        migration._prepare()
        migration.action_cancel()
        self.assertEqual(migration.state, 'cancelled')
        run = []
        with patch.object(SaasSubprocessMixin, '_run_odoo_subprocess',
                          lambda *a, **k: run.append(1) or ''):
            self._line(migration, self.t1).run_line()
        self.assertEqual(self._line(migration, self.t1).state, 'skipped')
        self.assertEqual(run, [])  # no subprocess ran for a cancelled migration

    def test_advance_waits_for_an_incomplete_wave(self):
        migration = self._migration()
        migration._prepare()
        migration.write({'state': 'canary', 'current_wave': 0})
        # canary lines are still pending → the gate must not open the fleet
        enqueued = []
        with patch.object(type(migration), '_enqueue_wave',
                          lambda self, wave: enqueued.append(wave)):
            migration._advance()
        self.assertEqual(migration.state, 'canary')
        self.assertEqual(enqueued, [])

    def test_async_gate_progresses_wave_by_wave_to_done(self):
        migration = self._migration(wave_size=1)  # t1/t2/t3 → waves 1,2,3
        with self._mocked():
            # simulate the queue worker: enqueuing a wave runs its lines inline.
            def _fake_enqueue(mig, wave):
                for line in mig._wave_lines(wave):
                    line.run_line()
            with patch.object(type(migration), '_enqueue_wave', _fake_enqueue):
                migration.action_start()                 # runs the canary
                for _ in range(10):
                    if migration.state in ('done', 'failed', 'canary_failed'):
                        break
                    self.env['ncollection.fleet.migration']._cron_advance()
        self.assertEqual(migration.state, 'done')
        for tenant in (self.canary, self.t1, self.t2, self.t3):
            self.assertEqual(self._line(migration, tenant).state, 'done')

    def test_double_start_is_rejected(self):
        migration = self._migration()
        with self._mocked():
            migration.action_run_sync()
        with self.assertRaises(UserError):
            migration.action_run_sync()          # already started

    def test_provisioning_shares_the_mixins_safety_constants(self):
        """One definition, not two copies kept in step by a test.

        This replaces test_db_name_guard_matches_provisioning, which asserted
        that provisioning's OWN copies of DB_NAME_RE / RESERVED_DB_NAMES equalled
        the mixin's. Those copies are gone (#243), so equality is now structural
        rather than something a test has to police — asserting identity is what
        proves the duplication is actually absent, not merely synchronised.
        """
        # Relative import: pylint-odoo W8150 flags an absolute
        # odoo.addons.<own module> import from inside the same module.
        from ..models import provisioning_job as pj
        self.assertIs(pj.DB_NAME_RE, DB_NAME_RE)
        self.assertIs(pj.RESERVED_DB_NAMES, RESERVED_DB_NAMES)

    def test_provisioning_now_carries_the_mixin(self):
        """The refactor's actual claim: the helpers come from the mixin."""
        job = self.env['ncollection.provisioning.job']
        self.assertIn('ncollection.saas.subprocess.mixin', job._inherit)
        for helper in ('_odoo_conn_args', '_run_odoo_subprocess',
                       '_db_conn_params', '_drop_database',
                       '_assert_safe_db_name'):
            self.assertTrue(hasattr(job, helper),
                            "provisioning lost %s in the refactor" % helper)

    def test_the_mixin_did_not_spawn_a_stray_model(self):
        """Pins the _name trap that this refactor walked into once.

        MetaModel only defaults _name from _inherit when _inherit is a STRING.
        With the list form and no explicit _name, ProvisioningJob would derive
        'provisioning.job' from its CLASS name and quietly become a separate,
        empty model — leaving ncollection.provisioning.job without action_run.
        The failure showed up as a view error, far from the cause, so assert the
        stray model's absence directly.
        """
        self.assertNotIn('provisioning.job', self.env.registry,
                         "ProvisioningJob registered under its class-derived "
                         "name — the explicit _name was lost")

    def test_the_two_name_guards_differ_on_existence_deliberately(self):
        """The one thing that must NOT be unified (#243).

        Provisioning CREATES a database, so an existing name is a collision it
        must reject. The mixin's guard is used by fleet migration, which
        UPGRADES databases that must ALREADY exist. Merging them silently breaks
        one caller or the other — provisioning would build over a live tenant
        DB, or a migration would refuse every real target. Neither is loud, so
        it is pinned here.
        """
        job = self.env['ncollection.provisioning.job']
        with patch.object(type(job), '_database_exists', return_value=True):
            # The mixin's guard accepts an existing database...
            job._assert_safe_db_name('existingtenant')
            # ...while provisioning's rejects it as a collision.
            with self.assertRaises(ValidationError):
                job._validate_db_name('existingtenant')

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
