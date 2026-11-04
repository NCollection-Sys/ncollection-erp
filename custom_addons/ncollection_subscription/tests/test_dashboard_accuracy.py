# -*- coding: utf-8 -*-
"""DASH-02: the Platform Administration summary reconciled against hand-worked fixtures.

  M  Monthly revenue adds plans in ONE currency (the platform company's), converted at today's rate - a plan priced in
     EUR is not added to a plan priced in the company currency as if the numbers were comparable.
  E  "Expiring soon" is the next 30 days; a subscription whose end date has already passed has lapsed, it is not
     "expiring soon" (it is surfaced by the at-risk monitor instead).
"""
from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestPlatformDashboardAccuracy(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.today = fields.Date.context_today(cls.env.user)
        cls.eur = cls.env.ref('base.EUR')
        cls.eur.active = True
        # the LATEST rate wins, so ours is dated today: 1 company-currency unit = 2 EUR, whatever else exists
        cls.env['res.currency.rate'].search([('currency_id', '=', cls.eur.id), ('name', '>=', cls.today),
                                             ('company_id', '=', cls.env.company.id)]).unlink()
        cls.env['res.currency.rate'].create({
            'name': cls.today, 'currency_id': cls.eur.id, 'rate': 2.0, 'company_id': cls.env.company.id})
        Plan = cls.env['ncollection.subscription.plan']
        cls.home_plan = Plan.create({'name': 'Acc Home', 'code': 'ACCHOME', 'monthly_price': 100.0,
                                     'yearly_price': 1200.0, 'max_users': 5})
        cls.eur_plan = Plan.create({'name': 'Acc Euro', 'code': 'ACCEUR', 'monthly_price': 200.0,
                                    'yearly_price': 2400.0, 'max_users': 5, 'currency_id': cls.eur.id})
        cls.seq = 0

    def _sub(self, plan, status='active', end=None):
        type(self).seq += 1
        tenant = self.env['ncollection.tenant'].create({
            'company_name': 'Acc %d' % self.seq, 'plan_id': plan.id, 'status': 'active',
            'database_status': 'ready', 'database_name': 'accdash%d' % self.seq})
        return self.env['ncollection.subscription'].create({
            'name': 'ACC-%d' % self.seq, 'tenant_id': tenant.id, 'plan_id': plan.id, 'status': status,
            'billing_cycle': 'monthly', 'start_date': self.today - timedelta(days=100), 'end_date': end})

    def _summary(self):
        return self.env['ncollection.subscription.dashboard'].create({})

    def test_m_monthly_revenue_adds_one_currency(self):
        base = self._summary().monthly_revenue
        self._sub(self.home_plan)
        self._sub(self.eur_plan)               # 200 EUR = 100 in the company currency
        self.assertAlmostEqual(self._summary().monthly_revenue - base, 200.0, 2,
                               "100 + (200 EUR = 100) = 200, never 100 + 200")

    def test_e_a_lapsed_subscription_is_not_expiring_soon(self):
        base = self._summary().expiring_soon
        self._sub(self.home_plan, end=self.today - timedelta(days=3))     # already past
        self.assertEqual(self._summary().expiring_soon, base)
        self._sub(self.home_plan, end=self.today + timedelta(days=10))    # genuinely soon
        self._sub(self.home_plan, end=self.today + timedelta(days=45))    # not yet
        self.assertEqual(self._summary().expiring_soon, base + 1)
