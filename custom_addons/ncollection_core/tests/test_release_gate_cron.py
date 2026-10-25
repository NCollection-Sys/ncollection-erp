# -*- coding: utf-8 -*-
"""The release gate's cron seam (release_gate.py, ``IrCron._process_jobs`` / ``IrCron._acquire_one_job``): a database
the code is ahead of runs no background work.

``_process_jobs_gate`` and ``_acquire_one_job_gate`` are what the wrapped static methods run outside test mode (Odoo's
test runner installs modules with the gate off, as for HTTP). The original handed to them here is a recorder, and
since only the original opens a database for its jobs and loads the registry (``_process_jobs_loop``), "the original
never ran" IS "no registry loaded, no job executed". The same with Odoo's real cron code against real databases (no
recorder) is scripts/upgrade/verify_upgrade.sh arm 9.
"""
from types import SimpleNamespace
from unittest.mock import patch

import psycopg2

from odoo.tests import TransactionCase, tagged

from odoo.addons.ncollection_core import release_gate as gate

from .test_release_gate import CURRENT, RELEASE, _with


def _stale():
    return gate.evaluate(_with(CURRENT, ncollection_core=('installed', '19.0.1.1.0')), None, False, RELEASE)


def _ready():
    return gate.evaluate(CURRENT, None, False, RELEASE)


@tagged('post_install', '-at_install')
class TestReleaseGateCrons(TransactionCase):

    def setUp(self):
        super().setUp()
        self.ran = []
        self.addCleanup(gate._cron_logged.clear)

    def _original(self, db_name):
        self.ran.append(db_name)
        return 'ran'

    def _states(self, **by_db):
        """``workspace_state`` answering an ``evaluate`` result per database name."""
        return patch.object(gate, 'workspace_state', side_effect=lambda db, cr=None: by_db[db])

    def _run(self, *dbs):
        return [gate._process_jobs_gate(db, self._original) for db in dbs]

    def test_the_cron_seam_is_the_one_this_gate_was_written_for_and_is_wrapped_once(self):
        from odoo.addons.base.models.ir_cron import IrCron
        self.assertIsNone(gate.cron_seam_problem())
        self.assertTrue(gate.cron_installed(), 'ncollection_core is server-wide: the cron gate must be in place')
        wrapped = (IrCron._process_jobs, IrCron._acquire_one_job)
        gate.install_cron()
        self.assertEqual((IrCron._process_jobs, IrCron._acquire_one_job), wrapped, 'never wrapped twice')
        for function in wrapped:
            self.assertFalse(getattr(function.__wrapped__, '_nc_release_gate', False))

    def test_a_stale_database_runs_no_cron_and_loads_no_registry(self):
        with self._states(stale1=_stale()), self.assertLogs(gate.__name__, level='WARNING') as logs:
            self.assertEqual(self._run('stale1'), [None])
        self.assertEqual(self.ran, [], 'the original (which loads the registry and runs the jobs) never ran')
        self.assertIn('db=stale1', logs.output[0])
        self.assertIn('->UPGRADE_REQUIRED crons NOT run', logs.output[0])
        self.assertIn('required=ncollection_core', logs.output[0])

    def test_the_real_state_of_this_database_decides_its_crons(self):
        """Not a recorded state: this database's own module table, read with plain SQL, made stale in-transaction."""
        db = self.env.cr.dbname
        self.addCleanup(gate.invalidate, db)
        self.assertTrue(gate.cron_allowed(db, self.env.cr), 'the test database matches its code')
        self.env.cr.execute("UPDATE ir_module_module SET latest_version = '19.0.0.0.1' "
                            "WHERE name = 'ncollection_core'")
        with self.assertLogs(gate.__name__, level='WARNING'):
            self.assertFalse(gate.cron_allowed(db, self.env.cr))

    def test_only_ready_runs_crons_no_hold_is_passable_here(self):
        """UPGRADING, UPGRADE_FAILED, BLOCKED and PROVISIONING - including a compatible database HELD for
        provisioning or upgrading, which the HTTP gate lets the platform's bearer through: nothing validates a release
        through a cron, so no hold lets one run."""
        cases = {
            'upgrading_hold': gate.evaluate(CURRENT, 'upgrading', False, RELEASE),
            'upgrading_rows_held': gate.evaluate(_with(CURRENT, ncollection_auth=('to upgrade', '19.0.1.3.0')),
                                                 'upgrading', False, RELEASE),
            'failed': gate.evaluate(CURRENT, 'upgrade_failed:migration crashed', False, RELEASE),
            'blocked_hold': gate.evaluate(CURRENT, 'blocked:operator', False, RELEASE),
            'blocked_gap': gate.evaluate(CURRENT, None, True, RELEASE),
            'blocked_missing': gate.evaluate(CURRENT + [('ncollection_gone', 'installed', '19.0.1.0.0')], None,
                                             False, RELEASE),
            'provisioning_hold': gate.evaluate(CURRENT, 'provisioning', False, RELEASE),
            'provisioning_new': gate.evaluate(None, None, False, RELEASE),
        }
        self.assertTrue(cases['provisioning_hold']['passable'] and cases['upgrading_hold']['passable'],
                        'precondition: these two ARE passable over HTTP')
        expected = {'upgrading_hold': gate.UPGRADING, 'upgrading_rows_held': gate.UPGRADING,
                    'failed': gate.UPGRADE_FAILED,
                    'blocked_hold': gate.BLOCKED, 'blocked_gap': gate.BLOCKED, 'blocked_missing': gate.BLOCKED,
                    'provisioning_hold': gate.PROVISIONING, 'provisioning_new': gate.PROVISIONING}
        with self._states(**cases), self.assertLogs(gate.__name__, level='WARNING'):
            for db, result in cases.items():
                with self.subTest(db=db):
                    self.assertEqual(result['state'], expected[db])
                    self.assertIsNone(gate._process_jobs_gate(db, self._original))
        self.assertEqual(self.ran, [])
        with self._states(ready=_ready()):
            self.assertEqual(self._run('ready'), ['ran'])
        self.assertEqual(self.ran, ['ready'], 'a READY database runs its crons through the original, unchanged')

    def test_rows_left_mid_upgrade_reach_odoo_s_own_check_and_still_no_job(self):
        """A killed install / ``-u`` leaves ``to upgrade`` rows and nothing else: Odoo's own ``_check_modules_state``
        (skip, or its reset after MAX_FAIL_TIME) still sees the database - plain SQL, no registry - while every JOB
        stays behind the per-job gate. Anything more than those rows (a hold, a module behind) is never handed
        over."""
        left = gate.evaluate(_with(CURRENT, ncollection_auth=('to upgrade', '19.0.1.3.0')), None, False, RELEASE)
        behind_too = gate.evaluate(_with(CURRENT, ncollection_auth=('to upgrade', '19.0.1.3.0'),
                                         ncollection_core=('installed', '19.0.1.1.0')), None, False, RELEASE)
        self.assertEqual(left['state'], gate.UPGRADING)
        with self._states(killed=left, killedbehind=behind_too), self.assertLogs(gate.__name__, 'WARNING'):
            self.assertEqual(self._run('killed', 'killedbehind'), ['ran', None])
            acquired = []
            cr = SimpleNamespace(dbname='killed')
            self.assertIsNone(gate._acquire_one_job_gate(cr, 3, lambda *a, **k: acquired.append(a)))
        self.assertEqual(self.ran, ['killed'])
        self.assertEqual(acquired, [], 'no job of a database that is not READY')

    def test_one_stale_tenant_never_stops_a_ready_one(self):
        """The cron runner polls every database in turn in one thread: a stale one is skipped, the next one runs."""
        with self._states(tenanta=_stale(), tenantb=_ready(), tenantc=_stale()), \
                self.assertLogs(gate.__name__, level='WARNING'):
            self.assertEqual(self._run('tenanta', 'tenantb', 'tenantc'), [None, 'ran', None])
        self.assertEqual(self.ran, ['tenantb'])

    def test_each_job_is_re_checked_before_its_registry_loads(self):
        """A hold or an ``-u`` that begins while one poll is still running its jobs stops the NEXT job. The manual
        "Run" button (include_not_ready) is an HTTP request the HTTP gate already decided."""
        acquired = []

        def acquire(cr, job_id, include_not_ready=False):
            acquired.append((job_id, include_not_ready))
            return {'id': job_id}

        cr = SimpleNamespace(dbname='heldmidpoll')
        with self._states(heldmidpoll=gate.evaluate(CURRENT, 'upgrading', False, RELEASE)), \
                self.assertLogs(gate.__name__, level='WARNING'):
            self.assertIsNone(gate._acquire_one_job_gate(cr, 7, acquire))
            self.assertEqual(gate._acquire_one_job_gate(cr, 8, acquire, include_not_ready=True), {'id': 8})
        with self._states(heldmidpoll=_ready()):
            self.assertEqual(gate._acquire_one_job_gate(cr, 9, acquire), {'id': 9})
        self.assertEqual(acquired, [(8, True), (9, False)])

    def test_an_unreadable_state_runs_nothing_and_is_logged_once(self):
        with patch.object(gate, 'workspace_state', side_effect=psycopg2.OperationalError('gone')), \
                self.assertLogs(gate.__name__, level='WARNING') as logs:
            self.assertEqual(self._run('unreadable', 'unreadable'), [None, None])
        self.assertEqual(self.ran, [])
        self.assertEqual(len(logs.output), 1, 'once per change of decision, never once per poll')

    def test_a_skip_is_logged_once_per_change_and_the_resumption_is_logged(self):
        with patch.object(gate, '_logger') as logger:
            for result in (_stale(), _stale(), _stale(), _ready(), _ready()):
                with self._states(tenantx=result):
                    self._run('tenantx')
        self.assertEqual(logger.warning.call_count, 1, 'once per change of decision, never once per poll')
        self.assertEqual(logger.info.call_count, 1, 'the resumption is logged once')
        warning = logger.warning.call_args[0][0] % logger.warning.call_args[0][1:]
        info = logger.info.call_args[0][0] % logger.info.call_args[0][1:]
        self.assertIn('UPGRADE_REQUIRED crons NOT run', warning)
        self.assertIn('UPGRADE_REQUIRED->READY crons run again', info)
        self.assertEqual(self.ran, ['tenantx', 'tenantx'])

    def test_a_moved_cron_seam_is_refused_loudly_and_left_alone(self):
        from odoo.addons.base.models.ir_cron import IrCron

        class RegistryFirst:
            @staticmethod
            def _process_jobs(db_name):
                return RegistryFirst._process_jobs_loop(db_name)

            @staticmethod
            def _process_jobs_loop(cron_cr, *, job_ids=()):
                registry = Registry(cron_cr)  # noqa: F821 - never executed, only read
                return RegistryFirst._acquire_one_job(registry, 1)

            @staticmethod
            def _acquire_one_job(cr, job_id, *, include_not_ready=False):
                return None

        class Renamed:
            def _process_jobs(self, db_name):
                return db_name

        self.assertIn('before loading the registry', gate.cron_seam_problem(RegistryFirst, 'IrCron._process_jobs('))
        self.assertIn('is not a static method', gate.cron_seam_problem(Renamed, 'IrCron._process_jobs('))
        self.assertIn('no longer runs crons', gate.cron_seam_problem(IrCron, 'IrCron.process(db)'))
        wrapped = IrCron._process_jobs
        with patch.object(gate, 'cron_installed', return_value=False), \
                patch.object(gate, 'cron_seam_problem', return_value='moved'), \
                self.assertLogs(gate.__name__, level='CRITICAL') as logs:
            gate.install_cron()
        self.assertIs(IrCron._process_jobs, wrapped, 'never wrapped')
        self.assertIn('Release gate NOT INSTALLED on crons: moved', logs.output[0])


@tagged('post_install', '-at_install')
class TestReleaseGatePreload(TransactionCase):
    """``odoo.service.server.preload_registries``: a serving process started with ``-d`` (the provisioning runner,
    ``-d ncollection``) loads its registries at boot. A database that is not READY is left unloaded; the release step
    itself (``-i`` / ``-u`` / ``--test-enable``) is never gated."""

    def setUp(self):
        super().setUp()
        self.addCleanup(gate._cron_logged.clear)

    def _preload(self, dbnames, states, installing=False):
        loaded = []

        def original(names):
            loaded.extend(names)
            return 0

        with patch.object(gate, 'workspace_state', side_effect=lambda db, cr=None: states[db]), \
                patch.object(gate, '_installing', return_value=installing):
            rc = gate.gated_preload_registries(dbnames, original)
        return rc, loaded

    def test_the_preload_seam_is_installed_and_valid(self):
        from odoo.service import server
        self.assertIsNone(gate.preload_seam_problem())
        self.assertTrue(gate.preload_installed())
        wrapped = server.preload_registries
        gate.install_preload()
        self.assertIs(server.preload_registries, wrapped, 'never wrapped twice')

    def test_a_stale_database_is_not_preloaded_and_a_ready_one_is(self):
        with self.assertLogs(gate.__name__, level='WARNING') as logs:
            rc, loaded = self._preload(['platformx', 'tenantx'], {'platformx': _stale(), 'tenantx': _ready()})
        self.assertEqual((rc, loaded), (0, ['tenantx']))
        self.assertIn('db=platformx', logs.output[0])
        self.assertIn('registry NOT preloaded at start', logs.output[0])

    def test_the_release_step_itself_is_never_gated(self):
        """``odoo -d X -u <modules> --stop-after-init`` preloads with update_module: that IS the upgrade."""
        _rc, loaded = self._preload(['platformx'], {'platformx': _stale()}, installing=True)
        self.assertEqual(loaded, ['platformx'])

    def test_an_unreadable_database_is_left_to_odoo_s_own_preload_error(self):
        loaded = []
        with patch.object(gate, 'workspace_state', side_effect=psycopg2.OperationalError('no such database')), \
                patch.object(gate, '_installing', return_value=False):
            gate.gated_preload_registries(['nosuchdb'], lambda names: loaded.extend(names) or -1)
        self.assertEqual(loaded, ['nosuchdb'], "Odoo reports a database it cannot open, as before")

    def test_installing_reads_odoo_s_own_flags(self):
        with patch.dict(gate.config.options, {'test_enable': False, 'init': {}, 'update': {}, 'reinit': []}):
            self.assertFalse(gate._installing())
        with patch.dict(gate.config.options, {'test_enable': False, 'init': {}, 'update': {'ncollection_core': 1},
                                              'reinit': []}):
            self.assertTrue(gate._installing())

    def test_a_moved_preload_seam_is_refused(self):
        bare = SimpleNamespace(preload_registries=lambda dbnames: None)
        self.assertIsNotNone(gate.preload_seam_problem(bare))
