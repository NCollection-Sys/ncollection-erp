# -*- coding: utf-8 -*-
"""P3-T14: the fleet migration orchestrator — canary gate, rolling waves,
failure isolation (+ optional auto-restore), dry-run, and the audit log.

The per-tenant `odoo -u` subprocess, the pg_dump snapshot, and the drop/restore
are stubbed so the ORCHESTRATION logic is exercised deterministically without a
real tenant database.
"""
import contextlib
import os
import shutil
import tempfile
from unittest.mock import patch

from odoo.addons.ncollection_saas.models.backup import NcollectionBackup
from odoo.addons.ncollection_saas.models.saas_subprocess import (
    DB_NAME_RE,
    RESERVED_DB_NAMES,
    SaasSubprocessMixin,
)
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger


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
        drop_calls=None, restore_fail=False, installed=None, cmds=None,
    ):
        """Stub every external side effect: the odoo subprocess, the snapshot,
        and the drop/restore. `fail_dbs` makes the apply step raise for those
        DBs; `restore_fail` makes the restore raise (worst-case path).

        `installed` is {db: {module, ...}} — what the tenant ALREADY has, which
        the install path's pre-check reads (#218). Default None means "the
        state check returns no marker", which the engine treats as 'not
        satisfied' and proceeds, so every pre-existing test is unaffected.
        """
        def _run(_self, cmd, label, stdin=None, env=None, timeout=None):
            db = cmd[cmd.index('-d') + 1] if '-d' in cmd else '?'
            if cmds is not None:
                cmds.append(list(cmd))
            if run_calls is not None:
                run_calls.append((db, 'shell' if 'shell' in cmd else 'apply'))
            if 'shell' in cmd:
                # Two different shell scripts now use this path; answer the one
                # actually asked for rather than always the smoke probe.
                if 'NC_HAVE' in (stdin or ''):
                    if installed is None:
                        return ''            # no marker -> engine proceeds
                    have = ','.join(sorted(installed.get(db, ())))
                    return 'NC_HAVE=%s\n' % have
                return 'NC_SMOKE_OK=True\n'
            if ('-u' in cmd or '-i' in cmd) and db in fail_dbs:
                raise UserError(_self.env._("simulated apply failure on %s", db))
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

    # ---- manual restore in place (#244) ----------------------------------

    def _failed_line(self, with_file=True):
        """A tenant left FAILED with its snapshot kept — exactly the state
        `auto_restore=OFF` produces, which is what #244 exists to recover from.

        Built by driving the real failure path rather than by writing states
        directly, so the fixture cannot drift from what the engine actually
        produces.
        """
        migration = self._migration(auto_restore=False)
        with self._mocked(fail_dbs={self.t2.database_name}):
            migration.action_run_sync()
        line = self._line(migration, self.t2)
        self.assertEqual(line.state, 'failed')          # fixture sanity
        self.assertTrue(line.backup_id)
        if with_file:
            # The pre-flight checks the file is really on disk, so a happy-path
            # test has to put one there — asserting on a path that does not
            # exist would test the refusal, not the restore.
            tmpdir = tempfile.mkdtemp()
            self.addCleanup(shutil.rmtree, tmpdir, True)   # dir, not just file
            path = os.path.join(tmpdir, 'snap.enc')
            with open(path, 'wb') as fh:
                fh.write(b'x')
            line.backup_id.sudo().write({'file_path': path})
        return migration, line

    def test_restore_in_place_rolls_the_tenant_back(self):
        migration, line = self._failed_line()
        drops, restores = [], []
        with self._mocked(drop_calls=drops, restore_calls=restores):
            line.action_restore_in_place()

        self.assertEqual(line.state, 'restored')
        self.assertIn(self.t2.database_name, drops)
        self.assertIn(self.t2.database_name, restores)
        # The tenant was flagged 'error' by the failure; recovery must clear it,
        # or config-sync/backup/checkout keep refusing to treat it as live.
        self.assertEqual(self.t2.database_status, 'ready')
        # Recorded where an operator will look (the run's audit log).
        self.assertIn('Restored in place', migration.log)

    @mute_logger('odoo.addons.ncollection_saas.models.fleet_migration_line')
    def test_a_missing_snapshot_file_refuses_BEFORE_dropping(self):
        """The whole reason this button needs a pre-flight.

        `_restore` DROPS the database and only then calls `restore_to`, which
        checks the file_path FIELD, not the file. Pressed days after the run,
        with the file swept by retention, one click would destroy a live tenant
        and leave nothing to put back.
        """
        _migration, line = self._failed_line(with_file=False)
        drops, restores = [], []
        with self._mocked(drop_calls=drops, restore_calls=restores):
            # NOT assertRaises: if the guard is DELETED nothing raises, and
            # assertRaises fails at __exit__ before the drops assertion below is
            # ever reached — so the failure would read "UserError not raised"
            # rather than naming the actual damage. Structured so the
            # non-destruction check runs on every path.
            try:
                line.action_restore_in_place()
                raised = None
            except UserError as exc:
                raised = exc

        self.assertEqual(drops, [], "dropped the database despite refusing")
        self.assertEqual(restores, [])
        self.assertIsNotNone(raised, "did not refuse a missing snapshot")
        self.assertIn('REFUSING', str(raised))
        self.assertEqual(line.state, 'failed')          # untouched
        self.assertEqual(self.t2.database_status, 'error')

    @mute_logger('odoo.addons.ncollection_saas.models.fleet_migration_line')
    def test_a_restore_that_fails_after_the_drop_is_not_silent(self):
        """_drop_database commits on a SEPARATE autocommit connection, outside
        the ORM transaction. So when restore_to fails the database is already
        gone while the ORM rolls this method's writes back — leaving the line on
        its stale message and nothing pointing anyone at a destroyed tenant.

        The auto-restore path has handled this since P3-T14; the manual button
        must too. Asserts the bookkeeping SURVIVES, which is the whole point:
        an exception alone would be a toast nobody sees.
        """
        _migration, line = self._failed_line()
        before = len(line.migration_id.activity_ids)
        with self._mocked(restore_fail=True):
            action = line.action_restore_in_place()

        # Must NOT raise: raising rolls the ORM transaction back and discards
        # every record below, leaving a vanishing toast as the only trace of a
        # destroyed database. The operator gets a sticky danger notification and
        # the bookkeeping SURVIVES.
        self.assertEqual(action['params']['type'], 'danger')
        self.assertTrue(action['params']['sticky'])

        self.assertEqual(line.state, 'failed')
        self.assertIn('RESTORE FAILED', line.message)
        self.assertIn('may no longer exist', line.message)
        self.assertEqual(self.t2.database_status, 'error')
        self.assertGreater(len(line.migration_id.activity_ids), before,
                           "no recovery to-do scheduled for a destroyed tenant")

    def test_a_snapshot_from_another_tenant_is_refused(self):
        """tenant_id, state and backup_id are three independently writable
        fields with nothing binding them. Restoring across tenants would destroy
        live data AND serve one tenant's data under another's database name."""
        _migration, line = self._failed_line()
        other = self.env['ncollection.backup'].create({
            'tenant_id': self.t3.id, 'backup_type': 'daily'})
        line.sudo().write({'backup_id': other.id})
        drops = []
        with self._mocked(drop_calls=drops):
            with self.assertRaises(UserError) as ctx:
                line.action_restore_in_place()
        self.assertIn('REFUSING', str(ctx.exception))
        self.assertEqual(drops, [], "dropped a database using another tenant's snapshot")

    def test_an_empty_snapshot_file_is_refused(self):
        """os.path.isfile passes a 0-byte file — a truncated write or a disk that
        filled mid-backup. tenant_restore.sh would catch the empty result, but
        only AFTER the drop."""
        _migration, line = self._failed_line()
        with open(line.backup_id.file_path, 'wb'):
            pass                                    # truncate to 0 bytes
        drops = []
        with self._mocked(drop_calls=drops):
            with self.assertRaises(UserError) as ctx:
                line.action_restore_in_place()
        self.assertIn('empty', str(ctx.exception))
        self.assertEqual(drops, [])

    def test_the_composed_action_refuses_a_non_admin(self):
        """The unit test above pins _restore_assert_allowed in isolation; this
        pins that action_restore_in_place actually CALLS it. Without this,
        deleting the call would break no test in this file."""
        _migration, line = self._failed_line()
        called = []
        with patch.object(type(line), '_restore_assert_allowed',
                          lambda self: called.append(1)):
            with self._mocked():
                line.action_restore_in_place()
        self.assertEqual(called, [1], "the action never consulted the guard")

    def test_only_a_failed_line_can_be_restored(self):
        """Restoring a line that SUCCEEDED would roll back a good upgrade."""
        migration = self._migration()
        with self._mocked():
            migration.action_run_sync()
        good = self._line(migration, self.t1)
        self.assertEqual(good.state, 'done')            # fixture sanity
        with self.assertRaises(UserError) as ctx:
            good.action_restore_in_place()
        self.assertIn('FAILED line', str(ctx.exception))

    def test_a_line_with_no_snapshot_refuses(self):
        """A line that failed BEFORE its snapshot has nothing to restore — and
        its database was never modified, so there is nothing to undo either."""
        migration = self._migration(auto_restore=False)
        with self._mocked():
            migration._prepare()
        line = self._line(migration, self.t2)
        line.write({'state': 'failed'})
        self.assertFalse(line.backup_id)                # fixture sanity
        with self.assertRaises(UserError) as ctx:
            line.action_restore_in_place()
        self.assertIn('nothing to restore', str(ctx.exception))

    def test_the_orm_guard_refuses_a_non_admin(self):
        """Rule 4. A groups= attribute hides a button; it does not stop an RPC
        that DROPS A LIVE DATABASE.

        Calls the guard directly and matches a string only it produces. A bare
        assertRaises(UserError) here would be satisfied by Odoo's own model ACL
        — AccessError subclasses UserError — so it would pass with this guard
        deleted, which is how three tests on #221 fooled me.
        """
        user = self.env['res.users'].create({
            'name': 'Plain', 'login': 'restore-plain-user',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])],
        })
        line = self.env['ncollection.fleet.migration.line']
        with self.assertRaises(UserError) as ctx:
            line.with_user(user)._restore_assert_allowed()
        self.assertIn('Settings administrator', str(ctx.exception))

    def test_the_orm_guard_admits_a_settings_admin(self):
        """A guard that refused everyone would pass the test above and be
        useless. Uses a REAL group_system user, not uid=1 — the superuser would
        also satisfy a wrong implementation (env.su, _is_admin, or no check)."""
        admin = self.env['res.users'].create({
            'name': 'Settings Admin', 'login': 'restore-settings-admin',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id,
                                  self.env.ref('base.group_system').id])],
        })
        self.assertNotEqual(admin.id, self.env.ref('base.user_root').id)
        self.env['ncollection.fleet.migration.line'].with_user(
            admin)._restore_assert_allowed()

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

    # ---- install mode (#218) --------------------------------------------

    @staticmethod
    def _flags(cmds):
        """The -i/-u flags that actually reached a subprocess."""
        return [f for c in cmds for f in ('-i', '-u') if f in c]

    def test_upgrade_mode_still_uses_dash_u(self):
        """The regression guard. `operation` defaults to 'upgrade', so every
        migration written before #218 must behave exactly as it did."""
        migration = self._migration()
        self.assertEqual(migration.operation, 'upgrade')
        cmds = []
        with self._mocked(cmds=cmds):
            migration.action_run_sync()
        self.assertTrue(self._flags(cmds))
        self.assertEqual(set(self._flags(cmds)), {'-u'})

    def test_install_mode_uses_dash_i(self):
        """#218's whole point, asserted on the ACTUAL argv.

        Verified against odoo/modules/loading.py: -u selects only modules whose
        state is installed/to-upgrade, so on a tenant LACKING the module it is a
        silent no-op. A backfill run with -u would report success having
        installed nothing — the exact false-green this repo keeps hitting.
        """
        migration = self._migration(operation='install',
                                    module_names='ncollection_auth')
        cmds = []
        with self._mocked(cmds=cmds):
            migration.action_run_sync()
        self.assertTrue(self._flags(cmds))
        self.assertEqual(set(self._flags(cmds)), {'-i'})
        self.assertNotIn('-u', [f for c in cmds for f in c])

    def test_install_skips_a_tenant_that_already_has_the_module(self):
        """And skips it BEFORE snapshotting.

        -i is idempotent by itself (it selects only uninstalled modules), so
        running it would be harmless — but reaching it means taking a FULL
        BACKUP of a tenant that needs nothing. On a backfill that is most of the
        fleet.
        """
        migration = self._migration(operation='install',
                                    module_names='ncollection_auth')
        already = {'fleetone': {'ncollection_auth'}}
        cmds = []
        with self._mocked(installed=already, cmds=cmds):
            migration.action_run_sync()

        done = self._line(migration, self.t1)
        self.assertEqual(done.state, 'skipped')
        self.assertFalse(done.backup_id, "snapshotted a tenant it then skipped")
        self.assertIn('Already installed', done.message)
        # ...and the tenants that DID need it were not skipped.
        self.assertEqual(self._line(migration, self.t2).state, 'done')

    def test_a_skip_is_not_counted_as_a_failure(self):
        """The #221 lesson at fleet level: on a backfill, 'already has it' is the
        EXPECTED outcome for most tenants. Scoring it as a failure would make the
        run look catastrophic and train the operator to ignore the result.

        The first version of this test asserted ONLY failed_count == 0 and
        state == 'done' — which a fleet that skipped nothing and installed
        everywhere also produces, because _PASS_STATES already excluded
        'skipped' before #218. It could not tell "correctly skipped" from "the
        skip check never fired", so it would have passed with the whole feature
        deleted. Both reviewers caught it independently. It now asserts on
        evidence only the skip path can produce.
        """
        migration = self._migration(operation='install',
                                    module_names='ncollection_auth')
        everyone = {db: {'ncollection_auth'}
                    for db in ('fleetcanary', 'fleetone', 'fleettwo', 'fleetthree')}
        cmds = []
        with self._mocked(installed=everyone, cmds=cmds):
            migration.action_run_sync()

        self.assertEqual(migration.failed_count, 0,
                         "an all-skipped backfill was scored as failures")
        self.assertEqual(migration.state, 'done')
        # Evidence the skip actually happened, not just that nothing failed:
        self.assertTrue(migration.line_ids)
        self.assertTrue(all(line.state == 'skipped' for line in migration.line_ids),
                        "a tenant that already had the module was not skipped")
        self.assertFalse(any(line.backup_id for line in migration.line_ids),
                         "snapshotted tenants it then skipped")
        self.assertEqual(self._flags(cmds), [],
                         "an install subprocess ran for an already-satisfied fleet")

    @mute_logger('odoo.addons.ncollection_saas.models.fleet_migration_line')
    def test_a_failed_state_check_does_not_flag_a_healthy_tenant(self):
        """A failed READ must not take a tenant out of service.

        _already_satisfied runs BEFORE the snapshot, so if it raised, the generic
        failure path found no backup_id and called _flag_tenant_error() —
        marking a tenant that NOTHING had touched as 'error', which stops
        config-sync, backup and checkout treating it as live. A probe timeout on
        a busy server would have disabled a healthy tenant.

        Now the check swallows its own failure and proceeds; the install is
        idempotent, so the cost of being wrong is a no-op. If the tenant really
        is unreachable the INSTALL fails, and that failure legitimately flags it
        — by then a write was attempted.
        """
        migration = self._migration(operation='install',
                                    module_names='ncollection_auth')

        def _run(_self, cmd, label, stdin=None, env=None, timeout=None):
            if 'NC_HAVE' in (stdin or ''):
                raise UserError(_self.env._("simulated probe timeout"))
            if 'shell' in cmd:
                return 'NC_SMOKE_OK=True\n'
            return ''

        with patch.object(SaasSubprocessMixin, '_run_odoo_subprocess', _run), \
                patch.object(NcollectionBackup, 'run_backup', lambda s: s.write(
                    {'status': 'done', 'file_path': '/tmp/x.enc'})):
            migration.action_run_sync()

        for tenant in (self.canary, self.t1, self.t2, self.t3):
            self.assertNotEqual(
                tenant.database_status, 'error',
                "a failed READ flagged an untouched tenant as error")
            self.assertEqual(self._line(migration, tenant).state, 'done',
                             "the install did not proceed after a failed probe")

    def test_an_install_dry_run_names_the_real_flag(self):
        """A dry run exists to show what WOULD happen. Reporting `-u` on an
        install run misstates the single thing the operator is checking."""
        migration = self._migration(operation='install', dry_run=True,
                                    module_names='ncollection_auth')
        with self._mocked():
            migration.action_run_sync()
        message = self._line(migration, self.t1).message
        self.assertIn('-i', message)
        self.assertNotIn('-u', message)

    def test_an_unreadable_state_check_installs_rather_than_skipping(self):
        """Fail SAFE, and in the right direction.

        If the state check returns no marker we cannot tell whether the tenant
        has the module. Assuming "already installed" would silently skip a tenant
        the backfill exists to fix, and nothing would ever report it. Assuming
        "missing" costs an idempotent no-op. Choose the cheap wrong answer.
        """
        migration = self._migration(operation='install',
                                    module_names='ncollection_auth')
        cmds = []
        with self._mocked(installed=None, cmds=cmds):   # None -> no marker
            migration.action_run_sync()
        self.assertEqual(set(self._flags(cmds)), {'-i'},
                         "an unreadable state check skipped the install")
        self.assertEqual(self._line(migration, self.t1).state, 'done')

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
