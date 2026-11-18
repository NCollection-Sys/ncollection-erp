# -*- coding: utf-8 -*-
"""Moving a tenant to a plan that licenses MORE (its subscription's plan) installs the new applications, the way a plan
edit does (#461). Before, it only re-pushed licensing: the app showed as licensed but did not exist in the database."""
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestSubscriptionPlanChangeInstalls(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Plan = cls.env['ncollection.subscription.plan']
        cls.small = Plan.create({'name': 'Small', 'code': 'SPCSMALL', 'allowed_module_names': 'crm', 'max_users': 3})
        cls.big = Plan.create({'name': 'Big', 'code': 'SPCBIG', 'allowed_module_names': 'crm, mass_mailing',
                               'max_users': 3})
        cls.tenant = cls.env['ncollection.tenant'].create({
            'company_name': 'Plan Change Co', 'database_name': 'planchange', 'email': 'o@planchange.example',
            'plan_id': cls.small.id, 'status': 'active'})
        cls.sub = cls.env['ncollection.subscription'].create({
            'tenant_id': cls.tenant.id, 'plan_id': cls.small.id, 'status': 'draft'})
        cls.tenant.subscription_id = cls.sub

    def _change(self, plan):
        T = type(self.tenant)
        with patch.object(T, '_config_sync_enqueue') as sync, patch.object(T, '_nc_enqueue_module_install') as install:
            self.sub.write({'plan_id': plan.id})
        return sync, install

    def test_an_upgrade_licenses_and_installs(self):
        sync, install = self._change(self.big)
        self.assertEqual(self.tenant.plan_id, self.big)
        sync.assert_called_once()
        install.assert_called_once()

    def test_a_downgrade_licenses_only_and_never_installs(self):
        self.sub.with_context().write({'plan_id': self.big.id})
        sync, install = self._change(self.small)
        sync.assert_called_once()
        install.assert_not_called()
