# -*- coding: utf-8 -*-
"""Subscription billing engine (P2-T11) tests.

Acceptance: activating or renewing a subscription always produces exactly one
correct invoice. Tenants are marked 'ready' so the P2-T02 auto-provisioning
trigger (which also fires on activate) stays out of the way.
"""
from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestBilling(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.ref('base.main_company')
        cls.plan = cls.env['ncollection.subscription.plan'].create({
            'name': 'Starter', 'code': 'BILL_STARTER',
            'monthly_price': 100.0, 'yearly_price': 1000.0, 'max_users': 5,
        })
        cls.plan_pro = cls.env['ncollection.subscription.plan'].create({
            'name': 'Pro', 'code': 'BILL_PRO',
            'monthly_price': 300.0, 'yearly_price': 3000.0, 'max_users': 20,
        })
        cls.tenant = cls.env['ncollection.tenant'].create({
            'company_name': 'Bill Co', 'database_name': 'billco',
            'email': 'owner@billco.example', 'plan_id': cls.plan.id,
            'status': 'trial', 'database_status': 'ready',
        })

    def _new_sub(self, status='draft', plan=None, **kw):
        # Future-dated period so proration (which needs end_date > today) is
        # exercisable regardless of the calendar date the suite runs on.
        today = fields.Date.context_today(self.env.user)
        vals = {
            'tenant_id': self.tenant.id, 'plan_id': (plan or self.plan).id,
            'billing_cycle': 'monthly', 'status': status,
            'start_date': today, 'end_date': fields.Date.add(today, days=30),
        }
        vals.update(kw)
        return self.env['ncollection.subscription'].create(vals)

    # ---- setup prerequisites (decision A) --------------------------------

    def test_billing_setup_ready(self):
        self.assertTrue(self.company.chart_template, "admin company must have a COA")
        tax = self.company._nc_billing_tax()
        self.assertEqual(tax.amount, 5.0)
        self.assertEqual(tax.type_tax_use, 'sale')
        self.assertTrue(self.company._nc_billing_product())

    # ---- acceptance: exactly one correct invoice -------------------------

    def test_activate_creates_exactly_one_invoice(self):
        sub = self._new_sub()
        sub.action_activate()
        invoices = sub.invoice_ids.filtered(lambda m: m.move_type == 'out_invoice')
        self.assertEqual(len(invoices), 1, "activation must create exactly one invoice")
        inv = invoices
        self.assertEqual(inv.state, 'posted')
        self.assertEqual(inv.nc_tenant_id, self.tenant)
        self.assertEqual(inv.amount_untaxed, 100.0)
        self.assertEqual(inv.amount_tax, 5.0)      # 5% VAT
        self.assertEqual(inv.amount_total, 105.0)
        self.assertEqual(inv.partner_id, self.tenant.partner_id)

    def test_billing_is_idempotent_per_period(self):
        sub = self._new_sub()
        sub.action_activate()
        # a second bill for the same period must not create a second invoice
        sub._nc_bill_period('purchase', sub.start_date, sub.end_date)
        self.assertEqual(sub.invoice_count, 1)

    def test_renewal_creates_second_invoice(self):
        sub = self._new_sub()
        sub.action_activate()
        sub.action_renew()
        self.assertEqual(sub.invoice_count, 2, "renewal must add one more invoice")

    def test_payment_status_invoiced_after_activate(self):
        sub = self._new_sub()
        self.assertEqual(sub.payment_status, 'no_invoice')
        sub.action_activate()
        self.assertEqual(sub.payment_status, 'invoiced')

    # ---- proration on mid-cycle upgrade ----------------------------------

    def test_proration_on_upgrade(self):
        # active sub created directly (no purchase invoice) to isolate proration
        sub = self._new_sub(status='active')
        sub.write({'plan_id': self.plan_pro.id})
        prorations = sub.invoice_ids.filtered(
            lambda m: m.move_type == 'out_invoice' and m.amount_untaxed > 0)
        self.assertTrue(prorations, "a mid-cycle upgrade must raise a proration invoice")
        # full remaining cycle: (300 - 100) prorated over the whole period ~= 200
        self.assertAlmostEqual(prorations[:1].amount_untaxed, 200.0, delta=10.0)

    def test_proration_denominator_stable_after_renewal(self):
        # After a renewal the period is one cycle, not (start..extended-end):
        # a same-day upgrade must still bill at most one full period's diff (~200),
        # never a multiple of it (the pre-fix bug spanned every renewed cycle).
        sub = self._new_sub(status='active')
        sub.action_renew()  # extends end_date by a cycle; start_date unchanged
        sub.write({'plan_id': self.plan_pro.id})
        prorations = sub.invoice_ids.filtered(
            lambda m: 'proration' in (m.invoice_line_ids[:1].name or ''))
        self.assertTrue(prorations, "upgrade after renewal must raise a proration invoice")
        # With the tighter (single-cycle) denominator, only the [0, total_days]
        # clamp keeps this from billing a multiple of the period difference.
        self.assertLessEqual(prorations[:1].amount_untaxed, 200.0 + 1.0,
                             "proration must not exceed one full period's difference (~200)")

    def test_downgrade_raises_no_invoice(self):
        sub = self._new_sub(status='active', plan=self.plan_pro)
        before = sub.invoice_count
        sub.write({'plan_id': self.plan.id})   # pro -> starter = downgrade
        self.assertEqual(sub.invoice_count, before,
                         "a downgrade must not raise an immediate invoice")

    # ---- currency / pricing ----------------------------------------------

    def test_invoice_currency_is_aed(self):
        sub = self._new_sub()
        sub.action_activate()
        inv = sub.invoice_ids.filtered(lambda m: m.move_type == 'out_invoice')
        self.assertEqual(inv.currency_id.name, 'AED',
                         "UAE-VAT invoices must be billed in AED, not the USD default")

    def test_yearly_cycle_uses_yearly_price(self):
        sub = self._new_sub(billing_cycle='yearly')
        sub.action_activate()
        inv = sub.invoice_ids.filtered(lambda m: m.move_type == 'out_invoice')
        self.assertEqual(inv.amount_untaxed, 1000.0, "yearly cycle bills yearly_price")
        self.assertEqual(inv.amount_tax, 50.0)       # 5% of 1000

    # ---- payment status paths --------------------------------------------

    def test_partner_reused_across_invoices(self):
        sub = self._new_sub()
        sub.action_activate()
        sub.action_renew()
        partners = sub.invoice_ids.mapped('partner_id')
        self.assertEqual(len(partners), 1, "all invoices bill the same tenant partner")
        self.assertEqual(partners, self.tenant.partner_id)

    def test_overdue_when_due_date_passed(self):
        sub = self._new_sub()
        sub.action_activate()
        inv = sub.invoice_ids.filtered(lambda m: m.move_type == 'out_invoice')
        # force the invoice past due while unpaid
        inv.invoice_date_due = fields.Date.subtract(
            fields.Date.context_today(self.env.user), days=1)
        sub.invalidate_recordset(['payment_status'])
        self.assertEqual(sub.payment_status, 'overdue')

    def test_paid_status_after_payment(self):
        sub = self._new_sub()
        sub.action_activate()
        inv = sub.invoice_ids.filtered(lambda m: m.move_type == 'out_invoice')
        self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=inv.ids,
        ).create({}).action_create_payments()
        self.assertEqual(sub.payment_status, 'paid')
