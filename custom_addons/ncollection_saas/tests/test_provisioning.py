# -*- coding: utf-8 -*-
"""P2-T01 provisioning engine — CI-safe unit tests.

These cover the pure logic (validation, module set, quota, status transitions,
enqueue) WITHOUT spawning odoo subprocesses or creating real databases. The
full create->login->rollback path is proven locally (see
tests/test_provisioning_engine.py, tagged out of the default CI run, and the
evidence in the PR).
"""
from unittest.mock import patch

from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged

MODEL = 'ncollection.saas.provisioning'  # dotted path only used in messages
ENGINE = 'odoo.addons.ncollection_saas.models.provisioning_job.ProvisioningJob'


@tagged('post_install', '-at_install')
class TestProvisioningLogic(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Job = cls.env['ncollection.provisioning.job']
        cls.plan = cls.env['ncollection.subscription.plan'].create({
            'name': 'Test Plan', 'code': 'TEST',
            'allowed_module_names': 'crm, sale, crm',  # dup on purpose
            'max_users': 5,
        })
        cls.tenant = cls.env['ncollection.tenant'].create({
            'company_name': 'Acme Co', 'database_name': 'acme',
            'email': 'owner@acme.example', 'plan_id': cls.plan.id, 'status': 'trial',
        })
        cls.job = cls.env['ncollection.provisioning.job'].create({
            'tenant_id': cls.tenant.id, 'database_name': 'acme', 'status': 'queued',
        })

    # ---- name validation (security) --------------------------------------

    def test_valid_name_passes(self):
        # patch the collision check so the result never depends on which DBs
        # happen to exist on the test cluster (deterministic in CI + locally).
        with patch('%s._database_exists' % ENGINE, return_value=False):
            self.job._validate_db_name('clienta')

    def test_reserved_names_rejected(self):
        for bad in ('admin', 'postgres', 'template1', 'www', 'api', 'staging'):
            with self.assertRaises(ValidationError, msg="'%s' must be reserved" % bad):
                self.job._validate_db_name(bad)

    def test_malformed_names_rejected(self):
        for bad in ('Acme', 'a', 'ab', '1abc', 'a-b', 'a b', '../etc', 'drop;', ''):
            with self.assertRaises(ValidationError, msg="'%s' must be rejected" % bad):
                self.job._validate_db_name(bad)

    def test_collision_rejected(self):
        with patch('%s._database_exists' % ENGINE, return_value=True):
            with self.assertRaises(ValidationError):
                self.job._validate_db_name('clientb')

    # ---- module set ------------------------------------------------------

    def test_module_list_core_plus_plan_deduped(self):
        mods = self.job._module_list()
        self.assertEqual(mods[:3], ['base', 'ncollection_core', 'ncollection_branding'])
        self.assertIn('crm', mods)
        self.assertIn('sale', mods)
        self.assertEqual(mods.count('crm'), 1, "plan duplicates must be collapsed")

    # ---- quota (security) ------------------------------------------------

    def test_quota_blocks_over_limit(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'ncollection_saas.provisioning_quota_per_hour', '1')
        # setUpClass already made 1 job; add a 2nd this hour -> 2 > 1
        self.Job.create({'tenant_id': self.tenant.id, 'database_name': 'acme2'})
        with self.assertRaises(UserError):
            self.job._check_quota()

    def test_quota_zero_disables(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'ncollection_saas.provisioning_quota_per_hour', '0')
        self.job._check_quota()  # must not raise

    # ---- status transitions ----------------------------------------------

    def test_mark_done_transitions(self):
        self.tenant.onboarding_stage = 'signup'
        self.job._mark_done()
        self.assertEqual(self.job.status, 'done')
        self.assertTrue(self.job.completed_at)
        self.assertEqual(self.tenant.database_status, 'ready')
        self.assertEqual(self.tenant.onboarding_stage, 'setup')

    def test_mark_failed_transitions(self):
        self.job._mark_failed()
        self.assertEqual(self.job.status, 'failed')
        self.assertEqual(self.tenant.database_status, 'error')

    def test_log_appends(self):
        self.job.log = False
        self.job._append_log('first')
        self.job._append_log('second')
        self.assertIn('first', self.job.log)
        self.assertIn('second', self.job.log)

    # ---- enqueue (queue_job) ---------------------------------------------

    def test_action_run_enqueues_job(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'ncollection_saas.provisioning_quota_per_hour', '0')  # don't block
        before = self.env['queue.job'].search_count([])
        self.job.action_run()
        after = self.env['queue.job'].search_count([])
        self.assertEqual(after, before + 1, "action_run must enqueue one queue.job")

    def test_action_run_respects_quota(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'ncollection_saas.provisioning_quota_per_hour', '1')
        self.Job.create({'tenant_id': self.tenant.id, 'database_name': 'acme3'})
        with self.assertRaises(UserError):
            self.job.action_run()
