# -*- coding: utf-8 -*-
"""Tenant lifecycle transitions (P1-T07)."""

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestTenantLifecycle(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.plan = cls.env["ncollection.subscription.plan"].create(
            {"name": "Tenant Plan", "code": "TEN", "max_users": 5}
        )

    def _tenant(self, **vals):
        base = {"company_name": "T-Test", "plan_id": self.plan.id}
        base.update(vals)
        return self.env["ncollection.tenant"].create(base)

    # ---------------- valid transitions ----------------

    def test_activate_from_trial(self):
        t = self._tenant()
        t.action_activate()
        self.assertEqual(t.status, "active")

    def test_suspend_from_active(self):
        t = self._tenant(status="active")
        t.action_suspend()
        self.assertEqual(t.status, "suspended")

    def test_activate_from_suspended(self):
        t = self._tenant(status="suspended")
        t.action_activate()
        self.assertEqual(t.status, "active")

    def test_expire_from_trial(self):
        t = self._tenant()
        t.action_expire()
        self.assertEqual(t.status, "expired")

    def test_expire_from_active(self):
        t = self._tenant(status="active")
        t.action_expire()
        self.assertEqual(t.status, "expired")

    def test_expire_from_suspended(self):
        t = self._tenant(status="suspended")
        t.action_expire()
        self.assertEqual(t.status, "expired")

    # ---------------- invalid transitions ----------------

    def test_suspend_from_trial_rejected(self):
        with self.assertRaises(ValidationError):
            self._tenant().action_suspend()

    def test_activate_from_active_rejected(self):
        with self.assertRaises(ValidationError):
            self._tenant(status="active").action_activate()

    def test_activate_from_expired_rejected(self):
        with self.assertRaises(ValidationError):
            self._tenant(status="expired").action_activate()

    def test_suspend_from_expired_rejected(self):
        with self.assertRaises(ValidationError):
            self._tenant(status="expired").action_suspend()

    def test_expire_from_expired_rejected(self):
        with self.assertRaises(ValidationError):
            self._tenant(status="expired").action_expire()
