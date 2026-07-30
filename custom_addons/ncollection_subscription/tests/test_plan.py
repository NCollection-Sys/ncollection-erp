# -*- coding: utf-8 -*-
"""Plan constraints and module-list parsing (P1-T07)."""

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestSubscriptionPlan(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Plan = cls.env["ncollection.subscription.plan"]

    def _plan(self, **vals):
        base = {"name": "Test Plan"}
        base.update(vals)
        return self.Plan.create(base)

    def test_max_users_positive_ok(self):
        plan = self._plan(code="TST-OK", max_users=5)
        self.assertEqual(plan.max_users, 5)

    def test_max_users_zero_rejected(self):
        with self.assertRaises(ValidationError):
            self._plan(code="TST-Z", max_users=0)

    def test_max_users_negative_rejected(self):
        with self.assertRaises(ValidationError):
            self._plan(code="TST-N", max_users=-3)

    def test_allowed_module_list_parsing(self):
        plan = self._plan(code="TST-P", allowed_module_names=" crm, sale ,stock,,crm ")
        self.assertEqual(plan.get_allowed_module_list(), ["crm", "sale", "stock"])

    def test_allowed_module_list_empty(self):
        plan = self._plan(code="TST-E")
        self.assertEqual(plan.get_allowed_module_list(), [])

    def test_enterprise_plan_provisions_financial_stack(self):
        # P3-T01: a provisioned Enterprise tenant must get the interim OCA
        # financial reports, so the Enterprise plan's module set includes them —
        # the engine's _module_list installs get_allowed_module_list().
        plan = self.env.ref("ncollection_subscription.demo_plan_enterprise")
        modules = plan.get_allowed_module_list()
        for m in ("account", "account_financial_report", "ncollection_mis_templates"):
            self.assertIn(
                m, modules,
                "the Enterprise plan must provision %s (Trial Balance / reports)" % m)
