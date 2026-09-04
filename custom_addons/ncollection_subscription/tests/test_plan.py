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


@tagged("post_install", "-at_install")
class TestEnterprisePlanLicensesTheNativeFinancialStack(TransactionCase):
    """#467: the shipped ENTERPRISE plan actually names the native modules.

    Making them selectable is only half the fix — the default plan still named
    only `account`, `account_financial_report` and `ncollection_mis_templates`,
    so a fresh Enterprise tenant got the interim OCA reports and none of the
    native financial stack. This asserts the DATA, because that is the half a
    picker test cannot see.
    """

    NATIVE = (
        "ncollection_account_reports",
        "ncollection_account_dashboard",
        "ncollection_account_budget",
        "ncollection_account_localization_uae",
    )
    # The interim OCA bootstrap. #117 retires it; until then removing it here
    # would revoke working reports from every live Enterprise tenant, so its
    # continued presence is asserted rather than assumed.
    INTERIM = ("account", "account_financial_report", "ncollection_mis_templates")

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.plan = cls.env["ncollection.subscription.plan"].search(
            [("code", "=", "ENTERPRISE")], limit=1)

    def test_the_enterprise_plan_names_the_native_financial_apps(self):
        if not self.plan:
            self.skipTest("no ENTERPRISE plan on this database")
        licensed = self.plan.get_allowed_module_list()
        for name in self.NATIVE:
            self.assertIn(name, licensed,
                          "%s is not licensed on ENTERPRISE (#467)" % name)

    def test_the_interim_oca_reports_are_still_licensed(self):
        """Additive, not a swap: #117 owns the retirement, not this ticket."""
        if not self.plan:
            self.skipTest("no ENTERPRISE plan on this database")
        licensed = self.plan.get_allowed_module_list()
        for name in self.INTERIM:
            self.assertIn(name, licensed)

    def test_every_licensed_name_is_a_module_that_exists(self):
        """A typo here is invisible until a provisioning job fails on a
        customer's tenant: Ring 1 just ignores an unknown name, and the install
        job is the first thing that cares."""
        if not self.plan:
            self.skipTest("no ENTERPRISE plan on this database")
        Module = self.env["ir.module.module"].sudo()
        for name in self.plan.get_allowed_module_list():
            self.assertTrue(
                Module.search_count([("name", "=", name)]),
                "ENTERPRISE licenses %r, which is not a module on this "
                "addons path" % name)
