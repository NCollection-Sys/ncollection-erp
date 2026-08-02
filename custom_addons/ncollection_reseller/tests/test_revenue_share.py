# -*- coding: utf-8 -*-
"""Reseller revenue-share report aggregates sub-tenant MRR x share (P10-T09)."""
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestRevenueShare(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        partner = cls.env['res.partner'].create({'name': 'RevCo'})
        cls.reseller = cls.env['ncollection.reseller'].create({
            'name': 'RevCo', 'partner_id': partner.id, 'revenue_share_pct': 20.0})
        cls.plan = cls.env['ncollection.subscription.plan'].create({
            'name': 'Std', 'code': 'STD-REV', 'monthly_price': 100.0})
        cls.Tenant = cls.env['ncollection.tenant']
        cls.Sub = cls.env['ncollection.subscription']

    def _active_sub(self, name):
        tenant = self.Tenant.create({
            'company_name': name, 'database_name': name,
            'reseller_id': self.reseller.id, 'plan_id': self.plan.id})
        return self.Sub.create({
            'tenant_id': tenant.id, 'plan_id': self.plan.id,
            'billing_cycle': 'monthly', 'status': 'active'})

    def test_share_aggregates(self):
        self._active_sub('rev1')
        self._active_sub('rev2')
        row = self.env['ncollection.reseller.revenue.share'].search(
            [('reseller_id', '=', self.reseller.id)])
        self.assertEqual(len(row), 1)
        self.assertEqual(row.subtenant_count, 2)
        self.assertAlmostEqual(row.total_mrr, 200.0, places=2)
        self.assertAlmostEqual(row.revenue_share_pct, 20.0, places=2)
        # 20% of 200 = 40 owed monthly.
        self.assertAlmostEqual(row.share_amount, 40.0, places=2)

    def test_reseller_without_subtenants_zero(self):
        p = self.env['res.partner'].create({'name': 'Empty'})
        empty = self.env['ncollection.reseller'].create({
            'name': 'Empty', 'partner_id': p.id, 'revenue_share_pct': 50.0})
        row = self.env['ncollection.reseller.revenue.share'].search(
            [('reseller_id', '=', empty.id)])
        self.assertAlmostEqual(row.total_mrr, 0.0, places=2)
        self.assertAlmostEqual(row.share_amount, 0.0, places=2)
