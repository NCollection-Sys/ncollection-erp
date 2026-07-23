# -*- coding: utf-8 -*-
"""P2-T14 lifecycle & dunning scheduler — simulated-clock tests.

The daily sweep takes an injectable `today`, so every threshold is driven on a
deterministic clock and proven to fire exactly once per subscription (a re-run
on the same day is a no-op). Suspension goes through the guarded transition, so
the SaaS tenant-projection still applies in a full install.
"""
from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestLifecycleScheduler(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Sub = cls.env['ncollection.subscription']
        cls.plan = cls.env['ncollection.subscription.plan'].create({
            'name': 'Dun Plan', 'code': 'DUN_PLAN', 'monthly_price': 100.0,
            'yearly_price': 1000.0, 'max_users': 5, 'trial_days': 14, 'grace_days': 15})
        cls.tenant = cls.env['ncollection.tenant'].create({
            'company_name': 'Dun Co', 'database_name': 'dunco',
            'email': 'owner@dun.example', 'plan_id': cls.plan.id,
            'status': 'active', 'database_status': 'ready'})
        cls.today = fields.Date.context_today(cls.env.user)

    def _sub(self, status='active', **kw):
        end = kw.pop('end_date', fields.Date.add(self.today, days=30))
        vals = {'name': 'SUB-DUN', 'tenant_id': self.tenant.id, 'plan_id': self.plan.id,
                'billing_cycle': 'monthly', 'status': status,
                'start_date': fields.Date.subtract(end, days=40), 'end_date': end}
        vals.update(kw)   # explicit start_date/trial_end_date in kw still win
        sub = self.Sub.create(vals)
        self.tenant.subscription_id = sub
        return sub

    # ---- advance-warning emails (30/14/7/1) ------------------------------
    def test_expiry_warnings_fire_once_per_threshold(self):
        end = fields.Date.add(self.today, days=30)
        sub = self._sub(end_date=end)
        self.Sub._cron_lifecycle_sweep(today=fields.Date.subtract(end, days=30))
        self.assertEqual(sub.nc_warnings_sent, '30')
        self.Sub._cron_lifecycle_sweep(today=fields.Date.subtract(end, days=30))  # replay
        self.assertEqual(sub.nc_warnings_sent, '30', "same-day re-run must not re-send")
        self.Sub._cron_lifecycle_sweep(today=fields.Date.subtract(end, days=14))
        self.assertEqual(self._csv(sub.nc_warnings_sent), {'14', '30'})
        self.Sub._cron_lifecycle_sweep(today=fields.Date.subtract(end, days=1))
        self.assertEqual(self._csv(sub.nc_warnings_sent), {'1', '14', '30'})

    # ---- expiry after end_date + 48h buffer ------------------------------
    def test_within_buffer_stays_active(self):
        sub = self._sub(end_date=fields.Date.subtract(self.today, days=1))
        self.Sub._cron_lifecycle_sweep(today=self.today)
        self.assertEqual(sub.status, 'active', "within the 48h buffer the sub stays active")

    def test_expire_after_buffer(self):
        sub = self._sub(end_date=fields.Date.subtract(self.today, days=3))
        self.Sub._cron_lifecycle_sweep(today=self.today)
        self.assertEqual(sub.status, 'expired')

    # ---- suspension after the grace window -------------------------------
    def test_suspend_after_grace(self):
        # end 20d ago -> grace_end = end + 15 = 5d ago < today -> suspend
        sub = self._sub(status='expired', end_date=fields.Date.subtract(self.today, days=20))
        self.Sub._cron_lifecycle_sweep(today=self.today)
        self.assertEqual(sub.status, 'suspended')

    def test_within_grace_stays_expired(self):
        # end 5d ago -> grace_end = today + 10 -> still in grace
        sub = self._sub(status='expired', end_date=fields.Date.subtract(self.today, days=5))
        self.Sub._cron_lifecycle_sweep(today=self.today)
        self.assertEqual(sub.status, 'expired')

    # ---- trial expiry ----------------------------------------------------
    def test_trial_expires_past_trial_end(self):
        sub = self._sub(status='trial',
                        trial_end_date=fields.Date.subtract(self.today, days=1),
                        end_date=fields.Date.add(self.today, days=10))
        self.Sub._cron_lifecycle_sweep(today=self.today)
        self.assertEqual(sub.status, 'expired')

    # ---- dunning schedule ------------------------------------------------
    def test_dunning_fires_once_per_step(self):
        sub = self._sub(status='active', end_date=fields.Date.add(self.today, days=60))
        inv = sub._nc_bill_period('renewal', sub.start_date, fields.Date.add(self.today, days=60))
        inv.invoice_date_due = fields.Date.subtract(self.today, days=1)
        sub.invalidate_recordset(['payment_status'])
        self.assertEqual(sub.payment_status, 'overdue')
        due = inv.invoice_date_due
        self.Sub._cron_lifecycle_sweep(today=fields.Date.add(due, days=1))
        self.assertEqual(sub.nc_dunning_sent, '1')
        self.Sub._cron_lifecycle_sweep(today=fields.Date.add(due, days=1))  # replay
        self.assertEqual(sub.nc_dunning_sent, '1')
        self.Sub._cron_lifecycle_sweep(today=fields.Date.add(due, days=3))
        self.assertEqual(self._csv(sub.nc_dunning_sent), {'1', '3'})

    # ---- admin override to reactivate ------------------------------------
    def test_admin_reactivate(self):
        sub = self._sub(status='suspended', end_date=fields.Date.subtract(self.today, days=30))
        sub.action_reactivate()
        self.assertEqual(sub.status, 'active', "admin override reactivates a suspended sub")

    @staticmethod
    def _csv(value):
        return {x for x in (value or '').split(',') if x}
