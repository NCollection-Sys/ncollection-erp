# -*- coding: utf-8 -*-
"""Reseller revenue-share report aggregates sub-tenant MRR x share (P10-T09)."""
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestRevenueShare(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        partner = cls.env['res.partner'].create({'name': 'RevCo'})  # arch-guard: admin-db-platform
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

    def test_multi_currency_gives_one_row_per_currency(self):
        # Different-currency sub-tenants must never be summed as raw numbers:
        # the view returns one row per currency.
        eur = self.env.ref('base.EUR')
        eur.active = True
        plan_eur = self.env['ncollection.subscription.plan'].create({
            'name': 'Eur', 'code': 'EUR-REV', 'monthly_price': 50.0,
            'currency_id': eur.id})
        self._active_sub('mc_base')  # company-currency plan
        t = self.Tenant.create({
            'company_name': 'mc_eur', 'database_name': 'mceur',
            'reseller_id': self.reseller.id, 'plan_id': plan_eur.id})
        self.Sub.create({
            'tenant_id': t.id, 'plan_id': plan_eur.id,
            'billing_cycle': 'monthly', 'status': 'active'})
        rows = self.env['ncollection.reseller.revenue.share'].search(
            [('reseller_id', '=', self.reseller.id)])
        self.assertEqual(len(rows), 2)
        eur_row = rows.filtered(lambda r: r.currency_id == eur)
        self.assertAlmostEqual(eur_row.total_mrr, 50.0, places=2)
        self.assertAlmostEqual(eur_row.share_amount, 10.0, places=2)

    def test_reseller_without_subtenants_zero(self):
        # arch-guard: admin-db-platform -- see the note in
        # test_brand_cascade_payload: platform-side partner, required by
        # ncollection.reseller.partner_id, not tenant-database access.
        p = self.env['res.partner'].create(  # arch-guard: admin-db-platform
            {'name': 'Empty'})
        empty = self.env['ncollection.reseller'].create({
            'name': 'Empty', 'partner_id': p.id, 'revenue_share_pct': 50.0})
        row = self.env['ncollection.reseller.revenue.share'].search(
            [('reseller_id', '=', empty.id)])
        self.assertAlmostEqual(row.total_mrr, 0.0, places=2)
        self.assertAlmostEqual(row.share_amount, 0.0, places=2)
