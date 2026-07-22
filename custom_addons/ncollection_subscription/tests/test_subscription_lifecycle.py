# -*- coding: utf-8 -*-
"""Subscription lifecycle transitions, date constraint, days_remaining (P1-T07)."""

from datetime import timedelta

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestSubscriptionLifecycle(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.plan = cls.env["ncollection.subscription.plan"].create(
            {"name": "Life Plan", "code": "LIFE", "max_users": 5}
        )
        cls.tenant = cls.env["ncollection.tenant"].create(
            {"company_name": "Lifecycle Tenant", "plan_id": cls.plan.id}
        )

    def _sub(self, **vals):
        base = {
            "name": "SUB-TEST",
            "tenant_id": self.tenant.id,
            "plan_id": self.plan.id,
        }
        base.update(vals)
        return self.env["ncollection.subscription"].create(base)

    # ---------------- valid transitions ----------------

    def test_activate_from_draft(self):
        sub = self._sub()
        sub.action_activate()
        self.assertEqual(sub.status, "active")

    def test_expire_from_active(self):
        sub = self._sub(status="active")
        sub.action_expire()
        self.assertEqual(sub.status, "expired")

    def test_cancel_from_draft(self):
        sub = self._sub()
        sub.action_cancel()
        self.assertEqual(sub.status, "cancelled")

    def test_cancel_from_active(self):
        sub = self._sub(status="active")
        sub.action_cancel()
        self.assertEqual(sub.status, "cancelled")

    def test_renew_from_expired(self):
        sub = self._sub(status="expired")
        sub.action_renew()
        self.assertEqual(sub.status, "active")
        self.assertTrue(sub.end_date)

    def test_renew_from_active_extends_period(self):
        today = fields.Date.context_today(self.env["ncollection.subscription"])
        end = today + timedelta(days=10)
        sub = self._sub(status="active", end_date=end, billing_cycle="monthly")
        sub.action_renew()
        self.assertEqual(sub.status, "active")
        self.assertGreater(sub.end_date, end, "early renewal must extend, not shorten")

    # ---------------- invalid transitions ----------------

    def test_expire_from_draft_rejected(self):
        with self.assertRaises(ValidationError):
            self._sub().action_expire()

    def test_activate_from_active_rejected(self):
        with self.assertRaises(ValidationError):
            self._sub(status="active").action_activate()

    def test_activate_from_cancelled_rejected(self):
        with self.assertRaises(ValidationError):
            self._sub(status="cancelled").action_activate()

    def test_renew_from_draft_rejected(self):
        with self.assertRaises(ValidationError):
            self._sub().action_renew()

    def test_renew_from_cancelled_rejected(self):
        with self.assertRaises(ValidationError):
            self._sub(status="cancelled").action_renew()

    def test_cancel_from_expired_rejected(self):
        with self.assertRaises(ValidationError):
            self._sub(status="expired").action_cancel()

    def test_cancel_from_cancelled_rejected(self):
        with self.assertRaises(ValidationError):
            self._sub(status="cancelled").action_cancel()

    # ---------------- constraints & computes ----------------

    def test_end_date_before_start_rejected(self):
        today = fields.Date.context_today(self.env["ncollection.subscription"])
        with self.assertRaises(ValidationError):
            self._sub(start_date=today, end_date=today - timedelta(days=1))

    def test_end_date_equal_start_rejected(self):
        today = fields.Date.context_today(self.env["ncollection.subscription"])
        with self.assertRaises(ValidationError):
            self._sub(start_date=today, end_date=today)

    def test_days_remaining_future(self):
        today = fields.Date.context_today(self.env["ncollection.subscription"])
        sub = self._sub(end_date=today + timedelta(days=30))
        self.assertEqual(sub.days_remaining, 30)

    def test_days_remaining_no_end_date(self):
        self.assertEqual(self._sub().days_remaining, 0)

    def test_days_remaining_past(self):
        today = fields.Date.context_today(self.env["ncollection.subscription"])
        sub = self._sub(
            start_date=today - timedelta(days=60), end_date=today - timedelta(days=30)
        )
        self.assertEqual(sub.days_remaining, 0)

    # ---------------- chatter ----------------

    def test_status_change_posts_tracked_message(self):
        sub = self._sub()
        self.env.flush_all()
        self.env.cr.precommit.run()  # settle creation tracking first
        before = len(sub.message_ids)
        sub.action_activate()
        self.env.flush_all()
        self.env.cr.precommit.run()  # mail tracking finalizes in the precommit hook
        sub.invalidate_recordset(["message_ids"])
        new_messages = sub.message_ids.filtered(lambda m: m.subtype_id)
        self.assertGreater(len(sub.message_ids), before, "status change must log to chatter")
        self.assertIn(
            self.env.ref("ncollection_subscription.mt_subscription_status"),
            new_messages.mapped("subtype_id"),
            "tracked status change must use the custom subtype",
        )
