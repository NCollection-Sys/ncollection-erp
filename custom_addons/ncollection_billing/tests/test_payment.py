# -*- coding: utf-8 -*-
"""P2-T13 Stripe subscription payment collection.

Two layers of coverage:
- TestPaymentApply: the pure subscription effect (_nc_apply_payment /
  _nc_on_payment_failed) in a plain transaction — extend-to-cover, reactivation,
  idempotency, failure chatter.
- TestSubscriptionPaymentFlow: the real payment-framework flow via Odoo's
  AccountPaymentCommon — a confirmed transaction on a subscription invoice marks
  the invoice paid AND renews the subscription (the acceptance), once (idempotent
  against webhook replay / post-process re-run).
"""
from odoo import Command, fields
from odoo.tests import TransactionCase, tagged

from odoo.addons.account_payment.tests.common import AccountPaymentCommon


@tagged('post_install', '-at_install')
class TestPaymentApply(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.plan = cls.env['ncollection.subscription.plan'].create({
            'name': 'Pay Plan', 'code': 'PAY_PLAN',
            'monthly_price': 100.0, 'yearly_price': 1000.0, 'max_users': 5})
        cls.tenant = cls.env['ncollection.tenant'].create({
            'company_name': 'Pay Co', 'database_name': 'payco',
            'email': 'owner@payco.example', 'plan_id': cls.plan.id,
            'status': 'active', 'database_status': 'ready'})

    def _sub(self, status='active', end_date=None):
        today = fields.Date.context_today(self.env.user)
        sub = self.env['ncollection.subscription'].create({
            'name': 'SUB-PAY', 'tenant_id': self.tenant.id, 'plan_id': self.plan.id,
            'billing_cycle': 'monthly', 'status': status, 'start_date': today,
            'end_date': end_date or fields.Date.add(today, days=30)})
        self.tenant.subscription_id = sub
        return sub

    def _invoice_for(self, sub, period_end):
        return sub._nc_bill_period('renewal', sub.start_date, period_end)

    def test_apply_payment_extends_to_cover(self):
        today = fields.Date.context_today(self.env.user)
        sub = self._sub(end_date=fields.Date.add(today, days=30))
        far = fields.Date.add(today, days=60)
        inv = self._invoice_for(sub, far)
        sub._nc_apply_payment(inv)
        self.assertEqual(sub.end_date, far, "paying a later-period invoice extends the subscription")

    def test_apply_payment_never_shortens(self):
        today = fields.Date.context_today(self.env.user)
        end = fields.Date.add(today, days=60)
        sub = self._sub(end_date=end)
        near = fields.Date.add(today, days=10)
        inv = self._invoice_for(sub, near)
        sub._nc_apply_payment(inv)
        self.assertEqual(sub.end_date, end, "paying an already-covered period must not shorten")

    def test_apply_payment_reactivates_expired(self):
        today = fields.Date.context_today(self.env.user)
        sub = self._sub(status='expired', end_date=fields.Date.add(today, days=30))
        inv = self._invoice_for(sub, fields.Date.add(today, days=60))
        sub._nc_apply_payment(inv)
        self.assertEqual(sub.status, 'active', "payment recovers a lapsed subscription")

    def test_payment_failed_posts_chatter(self):
        sub = self._sub()
        before = len(sub.message_ids)
        fake_tx = self.env['payment.transaction']  # empty recordset stand-in
        # call directly with a lightweight object exposing reference/state
        sub._nc_on_payment_failed(type('T', (), {'reference': 'r1', 'state': 'error'})())
        self.assertGreater(len(sub.message_ids), before, "a failed payment is logged to chatter")
        _ = fake_tx


@tagged('post_install', '-at_install')
class TestSubscriptionPaymentFlow(AccountPaymentCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        company = cls.env.company
        company._nc_ensure_billing_setup()
        cls.plan = cls.env['ncollection.subscription.plan'].create({
            'name': 'Flow Plan', 'code': 'FLOW_PLAN',
            'monthly_price': 100.0, 'yearly_price': 1000.0, 'max_users': 5})
        # sudo(): this base (AccountPaymentCommon) runs as a non-super user, and a
        # 'ready' database_status is engine-only (#228 guard). A real 'ready'
        # tenant is only ever set by the provisioning engine (sudo) — mirror that.
        cls.tenant = cls.env['ncollection.tenant'].sudo().create({
            'company_name': 'Flow Co', 'database_name': 'flowco',
            'email': 'owner@flowco.example', 'plan_id': cls.plan.id,
            'status': 'active', 'database_status': 'ready'})

    def _paid_flow(self):
        today = fields.Date.context_today(self.env.user)
        sub = self.env['ncollection.subscription'].create({
            'name': 'SUB-FLOW', 'tenant_id': self.tenant.id, 'plan_id': self.plan.id,
            'billing_cycle': 'monthly', 'status': 'active', 'start_date': today,
            'end_date': fields.Date.add(today, days=30)})
        self.tenant.subscription_id = sub
        far = fields.Date.add(today, days=60)
        invoice = sub._nc_bill_period('renewal', sub.start_date, far)
        tx = self._create_transaction(
            flow='redirect', invoice_ids=[Command.set(invoice.ids)],
            partner_id=invoice.partner_id.id, amount=invoice.amount_total,
            currency_id=invoice.currency_id.id)
        return sub, invoice, tx, far

    def test_payment_marks_invoice_paid_and_renews(self):
        sub, invoice, tx, far = self._paid_flow()
        tx._set_done()
        tx._post_process()
        self.assertIn(invoice.payment_state, ('in_payment', 'paid'),
                      "a confirmed payment marks the subscription invoice paid")
        self.assertEqual(sub.end_date, far, "and renews (extends) the subscription")
        self.assertTrue(tx.nc_renewal_processed)

    def test_renewal_is_idempotent_on_replay(self):
        sub, invoice, tx, far = self._paid_flow()
        tx._set_done()
        tx._post_process()
        end_after_first = sub.end_date
        tx._post_process()   # webhook replay / post-process cron re-run
        self.assertEqual(sub.end_date, end_after_first,
                         "re-processing the same transaction must not renew twice")
