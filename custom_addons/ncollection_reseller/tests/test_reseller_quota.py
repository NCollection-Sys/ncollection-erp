# -*- coding: utf-8 -*-
"""Reseller provisioning quota is enforced at the ORM create layer (P10-T09)."""
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestResellerQuota(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({'name': 'Acme Partner'})  # arch-guard: admin-db-platform
        cls.Tenant = cls.env['ncollection.tenant']

    def _reseller(self, max_subtenants):
        return self.env['ncollection.reseller'].create({
            'name': 'Acme', 'partner_id': self.partner.id,
            'max_subtenants': max_subtenants,
        })

    def _make_tenant(self, reseller, name):
        return self.Tenant.create({
            'company_name': name, 'database_name': name.lower().replace(' ', ''),
            'reseller_id': reseller.id,
        })

    def test_quota_blocks_over_limit(self):
        reseller = self._reseller(max_subtenants=1)
        self._make_tenant(reseller, 'sub one')  # ok — within quota
        with self.assertRaises(ValidationError):
            self._make_tenant(reseller, 'sub two')  # N+1 blocked

    def test_quota_zero_is_unlimited(self):
        reseller = self._reseller(max_subtenants=0)
        for i in range(3):
            self._make_tenant(reseller, 'unlimited %s' % i)
        self.assertEqual(reseller.subtenant_count, 3)

    def test_multi_create_cannot_bypass(self):
        reseller = self._reseller(max_subtenants=1)
        with self.assertRaises(ValidationError):
            self.Tenant.create([
                {'company_name': 'a', 'database_name': 'a', 'reseller_id': reseller.id},
                {'company_name': 'b', 'database_name': 'b', 'reseller_id': reseller.id},
            ])

    def test_non_reseller_tenant_unaffected(self):
        # A plain tenant (no reseller) is never quota-gated.
        t = self.Tenant.create({'company_name': 'Direct', 'database_name': 'direct'})
        self.assertFalse(t.reseller_id)
