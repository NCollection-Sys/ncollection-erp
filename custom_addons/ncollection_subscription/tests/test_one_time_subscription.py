# -*- coding: utf-8 -*-
"""One Time (perpetual) memberships (#471).

The promise is "paid once, never expires, never renews, revocable only by an
administrator". Every recurring path in the platform is keyed on
`end_date != False`, so the promise reduces to one invariant — a One Time
subscription has NO end date — plus the guards that keep it that way. These
test the invariant, the guards, and that recurring subscriptions are untouched.
"""
from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestOneTimeSubscription(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.plan = cls.env['ncollection.subscription.plan'].create({
            'name': 'Perpetual', 'code': 'ONETIME1', 'max_users': 5,
            'monthly_price': 100.0, 'yearly_price': 1000.0,
            'one_time_price': 2500.0})
        cls.tenant = cls.env['ncollection.tenant'].create({
            'company_name': 'Perpetual Co', 'plan_id': cls.plan.id,
            'database_name': 'onetimeco'})

    def _sub(self, cycle='one_time', **kw):
        vals = {'name': 'SUB-OT', 'tenant_id': self.tenant.id,
                'plan_id': self.plan.id, 'billing_cycle': cycle}
        vals.update(kw)
        return self.env['ncollection.subscription'].create(vals)

    # ---- the invariant --------------------------------------------------

    def test_it_activates_with_no_end_date(self):
        """Acceptance 1-3: created, activated, and no expiry date is needed."""
        sub = self._sub()
        sub.action_activate()
        self.assertEqual(sub.status, 'active')
        self.assertFalse(sub.end_date)
        self.assertTrue(sub.is_one_time)

    def test_an_end_date_is_refused_outright(self):
        """The invariant is ENFORCED, not documented. An end date is exactly
        what would make a perpetual membership start behaving like a recurring
        one, and it could otherwise arrive from a form, an import or a payment
        webhook writing a period end."""
        sub = self._sub()
        with self.assertRaises(ValidationError):
            sub.end_date = fields.Date.context_today(self) + relativedelta(years=5)

    def test_switching_a_dated_subscription_to_one_time_is_refused(self):
        """The same invariant from the other direction — otherwise a monthly
        subscription could be converted and keep its expiry."""
        sub = self._sub(cycle='monthly')
        sub.action_activate()
        sub.action_renew()
        self.assertTrue(sub.end_date)
        with self.assertRaises(ValidationError):
            sub.billing_cycle = 'one_time'

    def test_no_grace_window_and_no_countdown(self):
        """Both are derived from end_date, so both must be empty — a perpetual
        membership that showed "0 days remaining" would read as expired."""
        sub = self._sub()
        sub.action_activate()
        self.assertFalse(sub.grace_end_date)
        self.assertEqual(sub.days_remaining, 0)

    def test_it_contributes_no_monthly_recurring_revenue(self):
        """Spreading a one-time price over months would inflate MRR with money
        that will never come again."""
        sub = self._sub()
        self.assertEqual(sub.mrr, 0.0)

    # ---- no renewal -----------------------------------------------------

    def test_renewal_is_refused(self):
        """Acceptance 4. Renewing would stamp an end date AND raise a second
        invoice for a customer who already paid in full."""
        sub = self._sub()
        sub.action_activate()
        with self.assertRaises(ValidationError):
            sub.action_renew()
        self.assertFalse(sub.end_date)

    # ---- time passing does not touch it ---------------------------------

    def test_time_passing_does_not_expire_or_suspend_it(self):
        """Acceptance 5-6, on a simulated clock ten years out. The lifecycle
        sweep is the only thing that expires or suspends a subscription."""
        sub = self._sub()
        sub.action_activate()
        Sub = self.env['ncollection.subscription']
        if not hasattr(Sub, '_cron_lifecycle_sweep'):
            self.skipTest('ncollection_billing is not installed on this database')
        far_future = fields.Date.context_today(self) + relativedelta(years=10)
        Sub._cron_lifecycle_sweep(today=far_future)
        self.assertEqual(sub.status, 'active',
                         'a perpetual membership must survive any amount of time')
        self.assertFalse(sub.end_date)

    # ---- an administrator can still revoke it ---------------------------

    def test_an_administrator_can_still_suspend_and_terminate(self):
        """Acceptance 7. 'No expiry' must not mean 'no way out'."""
        sub = self._sub()
        sub.action_activate()
        sub.action_suspend()
        self.assertEqual(sub.status, 'suspended')
        sub.action_terminate()
        self.assertEqual(sub.status, 'terminated')

    def test_an_administrator_can_still_cancel_it(self):
        sub = self._sub()
        sub.action_activate()
        sub.action_cancel()
        self.assertEqual(sub.status, 'cancelled')

    # ---- recurring subscriptions are untouched --------------------------

    def test_a_monthly_subscription_still_renews_and_expires(self):
        """Acceptance 8: the control. Every assertion above would also pass if
        the lifecycle had simply stopped working for everyone."""
        sub = self._sub(cycle='monthly')
        sub.action_activate()
        sub.action_renew()
        self.assertTrue(sub.end_date)
        self.assertTrue(sub.grace_end_date)
        self.assertFalse(sub.is_one_time)
        self.assertEqual(sub.mrr, 100.0)

    def test_a_yearly_subscription_still_prices_and_renews_normally(self):
        sub = self._sub(cycle='yearly')
        sub.action_activate()
        today = fields.Date.context_today(self)
        sub.action_renew()
        self.assertEqual(sub.end_date, today + relativedelta(years=1))
        self.assertAlmostEqual(sub.mrr, 1000.0 / 12.0, places=2)
