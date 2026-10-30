# -*- coding: utf-8 -*-
"""The tenant e-invoicing runner's scope (runner_scope.py): which databases OCA's jobrunner serves, and the patch.

The decision is pure and tested exhaustively; the patch is tested on a stand-in runner class with OCA's own shape
(``db_by_name``, ``channel_manager``, ``get_db_names``, ``process_notifications``), so the real class in this process
is never modified by a test."""

import os
from unittest.mock import patch

from odoo.tests import BaseCase, TransactionCase, tagged

from .. import runner_scope

READY_TENANT = {'role': 'tenant', 'gate_state': 'ready', 'installed': {'ncollection_uae_einvoicing', 'queue_job'},
                'subscription_status': 'active'}


@tagged('post_install', '-at_install')
class TestDecision(BaseCase):

    def test_a_ready_tenant_with_einvoicing_is_served(self):
        self.assertEqual(runner_scope.decide(READY_TENANT), (True, runner_scope.OK))

    def test_the_platform_is_never_served(self):
        self.assertFalse(runner_scope.decide(dict(READY_TENANT, role='platform'))[0])

    def test_a_database_the_gate_does_not_open_is_not_served(self):
        for state in ('provisioning', 'upgrading', 'upgrade_required', 'upgrade_failed', 'blocked', None):
            self.assertFalse(runner_scope.decide(dict(READY_TENANT, gate_state=state))[0], state)

    def test_a_tenant_without_einvoicing_is_not_served(self):
        self.assertFalse(runner_scope.decide(dict(READY_TENANT, installed={'queue_job'}))[0])
        self.assertFalse(runner_scope.decide(dict(READY_TENANT, installed={'ncollection_uae_einvoicing'}))[0])

    def test_a_suspended_or_expired_workspace_is_not_served(self):
        for status in ('suspended', 'Expired', ' cancelled '):
            self.assertFalse(runner_scope.decide(dict(READY_TENANT, subscription_status=status))[0], status)
        self.assertTrue(runner_scope.decide(dict(READY_TENANT, subscription_status='trial'))[0])
        self.assertTrue(runner_scope.decide(dict(READY_TENANT, subscription_status=None))[0])

    def test_an_unreachable_or_deleted_database_is_not_served(self):
        self.assertEqual(runner_scope.decide(None), (False, 'unreachable'))

    def test_the_eligible_set_is_exactly_the_eligible_databases(self):
        facts = {'tenanta': READY_TENANT, 'tenantb': dict(READY_TENANT, subscription_status='suspended'),
                 'platform': dict(READY_TENANT, role='platform'), 'gone': None,
                 'tenantc': READY_TENANT}
        self.assertEqual(runner_scope.eligible_databases(list(facts), facts.get), ['tenanta', 'tenantc'])


class _Channels:
    def __init__(self):
        self.notified, self.removed = [], []

    def notify(self, db_name, *job):
        self.notified.append((db_name, job))

    def remove_db(self, db_name):
        self.removed.append(db_name)


class _Db:
    closed = []

    def __init__(self, db_name):
        self.db_name, self.has_queue_job = db_name, True

    def close(self):
        _Db.closed.append(self.db_name)

    def select_jobs(self, where, args):
        db_name = self.db_name

        class _Cursor:
            def __enter__(self):
                return iter([('uuid-%s' % db_name, 'root.einvoice')])

            def __exit__(self, *exc):
                return False
        return _Cursor()


def _runner_class():
    class Runner:
        processed = 0

        def __init__(self):
            self.db_by_name, self.channel_manager = {}, _Channels()

        def get_db_names(self):
            return ['every', 'database']  # OCA's behaviour: all of them

        def process_notifications(self):
            Runner.processed += 1
    return Runner


@tagged('post_install', '-at_install')
class TestPatch(BaseCase):

    def test_without_the_flag_nothing_is_patched(self):
        Runner = _runner_class()
        with patch.dict(os.environ, {runner_scope.ENV_FLAG: ''}):
            self.assertFalse(runner_scope.install(Runner))
        self.assertEqual(Runner().get_db_names(), ['every', 'database'])

    def test_with_the_flag_the_runner_serves_and_refreshes_only_eligible_tenants(self):
        Runner = _runner_class()
        world = {'tenanta': READY_TENANT, 'platform': dict(READY_TENANT, role='platform')}
        clock = [1000.0]
        from odoo.addons.queue_job.jobrunner import runner as runner_mod

        def eligible(names, probe_fn=None):
            return sorted(name for name in world if runner_scope.decide(world[name])[0])

        with patch.dict(os.environ, {runner_scope.ENV_FLAG: '1'}), \
                patch.object(runner_scope, 'eligible_databases', eligible), \
                patch.object(runner_mod, 'Database', _Db):
            self.assertTrue(runner_scope.install(Runner, clock=lambda: clock[0]))
            self.assertTrue(runner_scope.install(Runner), 'idempotent')
            runner = Runner()
            self.assertEqual(runner.get_db_names(), ['tenanta'])
            runner.process_notifications()  # the first round reads the scope
            self.assertEqual(set(runner.db_by_name), {'tenanta'})
            self.assertEqual(runner.channel_manager.notified[0][0], 'tenanta', 'pending jobs of the joiner')
            # a new tenant appears, the first one is suspended: no restart needed, after the refresh interval
            world['tenantb'] = READY_TENANT
            world['tenanta'] = dict(READY_TENANT, subscription_status='suspended')
            runner.process_notifications()
            self.assertEqual(set(runner.db_by_name), {'tenanta'}, 'not before the interval')
            clock[0] += runner_scope.REFRESH_SECONDS
            runner.process_notifications()
            self.assertEqual(set(runner.db_by_name), {'tenantb'})
            self.assertIn('tenanta', runner.channel_manager.removed)
            # the tenant is deleted: it leaves the set
            del world['tenantb']
            clock[0] += runner_scope.REFRESH_SECONDS
            runner.process_notifications()
            self.assertEqual(runner.db_by_name, {})
            self.assertEqual(Runner.processed, 4, "OCA's own processing still runs every round")
            # a restart starts from the same decision
            self.assertEqual(Runner().get_db_names(), [])


@tagged('post_install', '-at_install')
class TestProbe(TransactionCase):

    def test_a_database_that_does_not_exist_is_unreachable_not_an_error(self):
        self.assertIsNone(runner_scope.probe('ncnosuchdatabase'))

    def test_this_database_is_probed_without_reading_business_data(self):
        facts = runner_scope.probe(self.env.cr.dbname)
        self.assertIsNotNone(facts)
        self.assertEqual(set(facts), {'role', 'gate_state', 'installed', 'subscription_status'})
        self.assertIn('ncollection_uae_einvoicing', facts['installed'])


class _VanishingDb(_Db):
    """A database that was eligible at the probe and is gone (or not yet usable) at connect time."""

    def __init__(self, db_name):
        if db_name.startswith('gone'):
            raise RuntimeError('database "%s" does not exist' % db_name)
        super().__init__(db_name)


@tagged('post_install', '-at_install')
class TestRunnerResilience(BaseCase):
    """Production hardening (Cycle 4): a refresh never takes the jobrunner loop down."""

    def _runner(self, world, database_cls=_VanishingDb):
        Runner = _runner_class()
        clock = [5000.0]
        from odoo.addons.queue_job.jobrunner import runner as runner_mod

        def eligible(names, probe_fn=None):
            if world.get('__unreadable__'):
                raise RuntimeError('the server is unavailable')
            return sorted(name for name in world if not name.startswith('__') and runner_scope.decide(world[name])[0])

        patches = [patch.dict(os.environ, {runner_scope.ENV_FLAG: '1'}),
                   patch.object(runner_scope, 'eligible_databases', eligible),
                   patch.object(runner_mod, 'Database', database_cls)]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        runner_scope.install(Runner, clock=lambda: clock[0])
        return Runner(), clock, Runner

    def test_a_database_that_vanishes_before_joining_is_skipped(self):
        runner, _clock, Runner = self._runner({'tenanta': READY_TENANT, 'gonetenant': READY_TENANT})
        runner.process_notifications()
        self.assertEqual(set(runner.db_by_name), {'tenanta'}, 'the other tenant is still served')
        self.assertEqual(Runner.processed, 1, "OCA's processing ran despite the failure")

    def test_an_unreadable_server_keeps_the_current_scope(self):
        world = {'tenanta': READY_TENANT}
        runner, clock, Runner = self._runner(world)
        runner.process_notifications()
        world['__unreadable__'] = True
        clock[0] += runner_scope.REFRESH_SECONDS
        runner.process_notifications()
        self.assertEqual(set(runner.db_by_name), {'tenanta'})
        self.assertEqual(Runner.processed, 2)

    def test_a_provisioning_tenant_joins_only_once_ready(self):
        world = {'tenanta': dict(READY_TENANT, gate_state='provisioning')}
        runner, clock, _Runner = self._runner(world)
        runner.process_notifications()
        self.assertEqual(runner.db_by_name, {})
        world['tenanta'] = READY_TENANT
        clock[0] += runner_scope.REFRESH_SECONDS
        runner.process_notifications()
        self.assertEqual(set(runner.db_by_name), {'tenanta'})
