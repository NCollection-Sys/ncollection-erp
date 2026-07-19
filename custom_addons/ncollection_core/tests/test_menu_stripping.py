# -*- coding: utf-8 -*-
"""Apps & Settings menu stripping — Owner lockdown (P1-T11).

Covers: Sales-role user sees no Apps/Settings menus; the Settings model
(res.config.settings) is denied for non-Owner at the ORM layer (core
group_system ACL, which Owner satisfies via implication); Owner retains
access.

Debug-mode stripping and DB-manager blocking are request-path / infra
concerns exercised by inspection + the live shell proof in the PR (they
cannot be driven through TransactionCase without a live HTTP request).
"""

from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestMenuStripping(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.apps_menu = cls.env.ref("base.menu_management")
        cls.settings_menu = cls.env.ref("base.menu_administration")
        cls.owner = new_test_user(
            cls.env, login="strip_owner", groups="base.group_user",
        )
        cls.owner.write({
            "group_ids": [(4, cls.env.ref("ncollection_core.group_role_owner").id)],
        })
        cls.sales = new_test_user(
            cls.env, login="strip_sales", groups="base.group_user",
        )
        cls.sales.write({
            "group_ids": [(4, cls.env.ref("ncollection_core.group_role_sales").id)],
        })

    # ---------------- menu group restriction ----------------

    def test_apps_menu_restricted_to_owner_group(self):
        groups = self.apps_menu.group_ids
        self.assertEqual(
            groups, self.env.ref("ncollection_core.group_role_owner"),
            "Apps menu must be restricted to the Owner role only",
        )

    def test_settings_menu_restricted_to_owner_group(self):
        groups = self.settings_menu.group_ids
        self.assertEqual(
            groups, self.env.ref("ncollection_core.group_role_owner"),
            "Settings menu must be restricted to the Owner role only",
        )

    def test_sales_user_sees_neither_menu(self):
        visible = self.env["ir.ui.menu"].with_user(self.sales)._visible_menu_ids()
        self.assertNotIn(self.apps_menu.id, visible)
        self.assertNotIn(self.settings_menu.id, visible)

    def test_owner_sees_both_menus(self):
        visible = self.env["ir.ui.menu"].with_user(self.owner)._visible_menu_ids()
        self.assertIn(self.apps_menu.id, visible)
        self.assertIn(self.settings_menu.id, visible)

    # ---------------- settings ORM mirror (Rule 4) ----------------

    def test_sales_denied_settings_model(self):
        """Non-Owner cannot touch res.config.settings (core group_system ACL)."""
        with self.assertRaises(AccessError):
            self.env["res.config.settings"].with_user(self.sales).create({})

    def test_owner_allowed_settings_model(self):
        """Owner implies group_system, so settings access is retained."""
        settings = self.env["res.config.settings"].with_user(self.owner).create({})
        self.assertTrue(settings)
