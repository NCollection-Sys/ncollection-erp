# -*- coding: utf-8 -*-
"""Balance Cloud Management side of unified Settings: plan defaults, overrides that only narrow, entitlement."""
import json
from unittest.mock import patch

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestSettingsPolicyPlatform(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.plan = cls.env['ncollection.subscription.plan'].create({'name': 'SP Plan', 'code': 'SPPLAN'})
        cls.tenant = cls.env['ncollection.tenant'].with_user(cls.env.ref('base.user_root')).create({
            'company_name': 'Policy Co', 'database_name': 'policyco', 'database_status': 'not_provisioned',
            'status': 'trial', 'plan_id': cls.plan.id})

    def _policy(self, modules=('ncollection_account_core', 'ncollection_audit', 'ncollection_ai')):
        with patch.object(type(self.tenant), '_nc_effective_module_list', return_value=list(modules)):
            return self.tenant._nc_settings_policy()

    def test_no_line_means_nothing_is_sent(self):
        self.assertEqual(self._policy(), {})
        with patch.object(type(self.tenant), '_nc_effective_module_list', return_value=[]):
            self.assertNotIn('settings_policy', self.tenant._config_sync_vals())

    def test_plan_default_then_tenant_override_narrows(self):
        Line = self.env['ncollection.settings.policy.line']
        Line.create({'plan_id': self.plan.id, 'section': 'company', 'level': 'edit'})
        Line.create({'tenant_id': self.tenant.id, 'section': 'company', 'level': 'view'})
        policy = self._policy()
        self.assertEqual(policy['company'], 'view')
        self.assertEqual(policy['users'], 'administer', "no line: the default")
        with patch.object(type(self.tenant), '_nc_effective_module_list', return_value=['ncollection_audit']):
            vals = self.tenant._config_sync_vals()
        self.assertEqual(json.loads(vals['settings_policy'])['company'], 'view')

    def test_an_override_cannot_widen_the_plan(self):
        Line = self.env['ncollection.settings.policy.line']
        Line.create({'plan_id': self.plan.id, 'section': 'roles', 'level': 'view'})
        with self.assertRaises(ValidationError):
            Line.create({'tenant_id': self.tenant.id, 'section': 'roles', 'level': 'administer'})

    def test_a_section_without_its_application_is_hidden(self):
        self.env['ncollection.settings.policy.line'].create(
            {'plan_id': self.plan.id, 'section': 'audit_log', 'level': 'administer'})
        self.assertEqual(self._policy(modules=())['audit_log'], 'hidden')
        self.assertEqual(self._policy()['audit_log'], 'administer')

    def test_changes_are_recorded_on_the_tenant(self):
        self.env['ncollection.settings.policy.line'].create(
            {'tenant_id': self.tenant.id, 'section': 'appearance', 'level': 'view'})
        self.assertIn('Settings policy set', self.tenant.message_ids[:1].body)

    def test_a_line_belongs_to_a_plan_or_a_tenant(self):
        with self.assertRaises(ValidationError):
            self.env['ncollection.settings.policy.line'].create({'section': 'company', 'level': 'view'})

    def test_receipt_capability_is_off_unless_granted(self):
        Line = self.env['ncollection.settings.policy.line']
        Line.create({'plan_id': self.plan.id, 'section': 'company', 'level': 'edit'})
        self.assertEqual(self._policy()['receipts'], 'denied', "a capability is never on by default")
        with self.assertRaises(ValidationError):           # the plan does not grant it: a tenant line cannot either
            Line.create({'tenant_id': self.tenant.id, 'section': 'receipts', 'level': 'view'})
        Line.create({'plan_id': self.plan.id, 'section': 'receipts', 'level': 'view'})
        self.assertEqual(self._policy()['receipts'], 'granted')
        self.assertEqual(self._policy(modules=())['receipts'], 'denied', "no accounting: nothing to grant")
        Line.create({'tenant_id': self.tenant.id, 'section': 'receipts', 'level': 'hidden'})
        self.assertEqual(self._policy()['receipts'], 'denied', "a tenant override can restrict it")

    def test_application_sections_follow_their_application(self):
        self.env['ncollection.settings.policy.line'].create(
            {'plan_id': self.plan.id, 'section': 'sales', 'level': 'administer'})
        self.assertEqual(self._policy()['sales'], 'hidden', "no Sales app licensed")
        self.assertEqual(self._policy(modules=('sale',))['sales'], 'administer')
