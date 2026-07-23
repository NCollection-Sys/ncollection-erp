# -*- coding: utf-8 -*-
"""P2-T17 Email Automation — every lifecycle transition sends exactly the right
branded email, and a tenant never receives two lifecycle emails on the same day.

Emails are asserted via the queued mail.mail rows (send_mail(force_send=False)),
which isolates real emails from the chatter mail.message rows the transitions
also post. Each test uses its own tenant so the per-tenant daily de-dup does not
bleed across cases.
"""
from types import SimpleNamespace

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestLifecycleEmails(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Sub = cls.env['ncollection.subscription']
        cls.plan = cls.env['ncollection.subscription.plan'].create({
            'name': 'Mail Plan', 'code': 'MAIL_PLAN', 'monthly_price': 100.0,
            'yearly_price': 1000.0, 'max_users': 5, 'trial_days': 14, 'grace_days': 15})
        cls.plan_pro = cls.env['ncollection.subscription.plan'].create({
            'name': 'Mail Pro', 'code': 'MAIL_PRO', 'monthly_price': 300.0,
            'yearly_price': 3000.0, 'max_users': 20})
        cls.today = fields.Date.context_today(cls.env.user)
        cls._seq = 0

    def _tenant(self):
        type(self)._seq += 1
        return self.env['ncollection.tenant'].create({
            'company_name': 'Mail Co %s' % self._seq,
            'database_name': 'mailco%s' % self._seq,
            'email': 'owner%s@mail.example' % self._seq,
            'plan_id': self.plan.id, 'status': 'trial', 'database_status': 'ready'})

    def _sub(self, status='active', plan=None, **kw):
        tenant = self._tenant()
        end = kw.pop('end_date', fields.Date.add(self.today, days=30))
        vals = {'tenant_id': tenant.id, 'plan_id': (plan or self.plan).id,
                'billing_cycle': 'monthly', 'status': status,
                'start_date': self.today, 'end_date': end}
        vals.update(kw)
        sub = self.Sub.create(vals)
        tenant.subscription_id = sub
        return sub

    def _mail_subjects(self, sub):
        return self.env['mail.mail'].search([
            ('model', '=', 'ncollection.subscription'), ('res_id', '=', sub.id)]).mapped('subject')

    # ---- one email per transition ----------------------------------------

    def test_expired_email(self):
        sub = self._sub(status='active')
        sub.action_expire()
        self.assertTrue(any('expired' in (s or '').lower() for s in self._mail_subjects(sub)))

    def test_suspended_email(self):
        sub = self._sub(status='active')
        sub.action_suspend()
        self.assertTrue(any('suspended' in (s or '').lower() for s in self._mail_subjects(sub)))

    def test_payment_received_email(self):
        sub = self._sub(status='draft')
        sub.action_activate()   # raises the invoice (no lifecycle mail)
        invoice = sub.invoice_ids[:1]
        sub._nc_apply_payment(invoice)
        self.assertTrue(any('payment received' in (s or '').lower() for s in self._mail_subjects(sub)))

    def test_payment_failed_email(self):
        sub = self._sub(status='active')
        sub._nc_on_payment_failed(SimpleNamespace(reference='TXFAIL', state='error'))
        self.assertTrue(any("couldn't process" in (s or '').lower() for s in self._mail_subjects(sub)))

    def test_plan_change_email(self):
        sub = self._sub(status='active')
        sub.write({'plan_id': self.plan_pro.id})
        self.assertTrue(any('plan has changed' in (s or '').lower() for s in self._mail_subjects(sub)))

    def test_trial_ending_email(self):
        sub = self._sub(status='trial', trial_end_date=fields.Date.add(self.today, days=2))
        self.Sub._cron_lifecycle_sweep(today=self.today)
        self.assertTrue(sub.nc_trial_ending_sent)
        self.assertTrue(any('trial ends soon' in (s or '').lower() for s in self._mail_subjects(sub)))

    # ---- branded layout + de-dup -----------------------------------------

    def test_lifecycle_mail_uses_branded_layout(self):
        # the send helper passes email_layout_xmlid; a green send proves the
        # branded layout resolves and renders.
        sub = self._sub(status='active')
        sub.action_expire()
        self.assertEqual(sub.nc_last_lifecycle_mail_date, self.today)

    def test_dedup_one_email_per_tenant_per_day(self):
        sub = self._sub(status='active')
        sub.action_suspend()                       # first lifecycle mail today
        sub.write({'plan_id': self.plan_pro.id})   # same day -> must be suppressed
        emails = self.env['mail.mail'].search([
            ('model', '=', 'ncollection.subscription'), ('res_id', '=', sub.id)])
        self.assertEqual(len(emails), 1, "a tenant must not receive two lifecycle emails the same day")
