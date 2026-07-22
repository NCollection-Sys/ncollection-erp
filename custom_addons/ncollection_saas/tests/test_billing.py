# -*- coding: utf-8 -*-
"""P2-T11 billing engine — CI-safe unit tests.

Prove the invoicing logic on the platform (admin) DB without spawning tenant
databases: every subscription here is pinned to database_status='ready' so
action_activate's provisioning trigger is a no-op and only the billing path
runs. The billing setup (UAE chart, 5% VAT, subscription product) is ensured
once in setUpClass — the same idempotent bootstrap the runtime uses.
"""
from odoo.tests import TransactionCase, tagged

from odoo.addons.ncollection_saas import ensure_billing_setup


@tagged('post_install', '-at_install')
class TestBilling(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Load the UAE chart + 5% VAT + subscription product on the platform
        # company (runtime does this lazily on first invoice; do it up-front here
        # so a fully-loaded registry makes the chart load take — see hooks.py).
        cls.tax, cls.product = ensure_billing_setup(cls.env)
        cls.Plan = cls.env['ncollection.subscription.plan']
        cls.Tenant = cls.env['ncollection.tenant']
        cls.Sub = cls.env['ncollection.subscription']
        cls.basic = cls.Plan.create({
            'name': 'Basic', 'code': 'basic',
            'monthly_price': 100.0, 'yearly_price': 1000.0, 'max_users': 5})
        cls.pro = cls.Plan.create({
            'name': 'Pro', 'code': 'pro',
            'monthly_price': 300.0, 'yearly_price': 3000.0, 'max_users': 20})

    def _make_sub(self, plan=None, cycle='monthly'):
        plan = plan or self.basic
        tenant = self.Tenant.create({
            'company_name': 'Acme LLC', 'email': 'ops@acme.test',
            'plan_id': plan.id, 'database_status': 'ready'})  # 'ready' => no provisioning
        sub = self.Sub.create({
            'name': 'SUB-ACME', 'tenant_id': tenant.id,
            'plan_id': plan.id, 'billing_cycle': cycle})
        tenant.subscription_id = sub
        return sub, tenant

    # ---- activation invoices one correct period --------------------------
    def test_activation_creates_one_posted_invoice(self):
        sub, tenant = self._make_sub()
        sub.action_activate()
        self.assertEqual(len(sub.invoice_ids), 1, "activation must create exactly one invoice")
        inv = sub.invoice_ids
        self.assertEqual(inv.state, 'posted')
        self.assertEqual(inv.move_type, 'out_invoice')
        self.assertEqual(inv.ncollection_subscription_id, sub)
        self.assertEqual(inv.ncollection_tenant_id, tenant)
        self.assertTrue(tenant.partner_id, "a billing partner is created for the tenant")
        self.assertEqual(inv.partner_id, tenant.partner_id)

    def test_activation_applies_5pct_vat_in_aed(self):
        sub, _ = self._make_sub()
        sub.action_activate()
        inv = sub.invoice_ids
        self.assertEqual(inv.currency_id.name, 'AED')
        self.assertAlmostEqual(inv.amount_untaxed, 100.0, places=2)
        self.assertAlmostEqual(inv.amount_tax, 5.0, places=2)   # 5% of 100
        self.assertAlmostEqual(inv.amount_total, 105.0, places=2)

    def test_yearly_cycle_prices_from_yearly_field(self):
        sub, _ = self._make_sub(cycle='yearly')
        sub.action_activate()
        inv = sub.invoice_ids
        self.assertAlmostEqual(inv.amount_untaxed, 1000.0, places=2)
        self.assertAlmostEqual(inv.amount_total, 1050.0, places=2)

    # ---- idempotency: exactly one invoice per period ---------------------
    def test_rebilling_same_period_is_idempotent(self):
        sub, _ = self._make_sub()
        sub.action_activate()
        first = sub.invoice_ids
        again = sub._create_subscription_invoice(period_key=str(sub.end_date))
        self.assertEqual(again, first, "same period returns the existing invoice")
        self.assertEqual(len(sub.invoice_ids), 1)

    # ---- renewal invoices the new period ---------------------------------
    def test_renewal_creates_a_second_invoice(self):
        sub, _ = self._make_sub()
        sub.action_activate()
        end_after_activation = sub.end_date
        sub.action_renew()
        self.assertGreater(sub.end_date, end_after_activation)
        self.assertEqual(len(sub.invoice_ids), 2, "renewal bills exactly one more period")

    # ---- proration on mid-cycle upgrade ----------------------------------
    def test_upgrade_creates_one_proration_invoice(self):
        sub, _ = self._make_sub()
        sub.action_activate()
        count_before = len(sub.invoice_ids)
        sub.plan_id = self.pro.id                       # mid-cycle upgrade basic -> pro
        prorations = sub.invoice_ids.filtered(
            lambda m: (m.ncollection_period_key or '').startswith('upgrade:'))
        self.assertEqual(len(prorations), 1, "an upgrade raises one proration invoice")
        self.assertEqual(len(sub.invoice_ids), count_before + 1)
        self.assertGreater(prorations.amount_total, 0.0)

    def test_downgrade_raises_no_immediate_invoice(self):
        sub, _ = self._make_sub(plan=self.pro)
        sub.action_activate()
        count_before = len(sub.invoice_ids)
        sub.plan_id = self.basic.id                     # downgrade -> no charge now
        self.assertEqual(len(sub.invoice_ids), count_before,
                         "a downgrade does not invoice mid-cycle")

    # ---- payment status is tracked on the subscription -------------------
    def test_payment_state_mirrors_invoice(self):
        sub, _ = self._make_sub()
        self.assertEqual(sub.invoice_payment_state, 'none')
        sub.action_activate()
        self.assertEqual(sub.invoice_payment_state, 'not_paid')
        inv = sub.invoice_ids
        self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=inv.ids
        ).create({}).action_create_payments()
        self.assertIn(sub.invoice_payment_state, ('paid', 'partial'))
        self.assertIn(inv.payment_state, ('in_payment', 'paid'),
                      "invoice is settled after registering payment")
