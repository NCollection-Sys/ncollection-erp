# -*- coding: utf-8 -*-
"""Menu visibility engine — Ring 1 tests (P1-T09).

Uses a synthetic app ("fake_app"): a root menu + child menu whose xml-ids
belong to that module, so the tests are independent of which real Odoo
apps are installed in the CI database.
"""

from odoo.tests import TransactionCase, tagged

from odoo.addons.ncollection_core.models import ir_ui_menu as menu_mod


@tagged("post_install", "-at_install")
class TestMenuVisibility(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Config = cls.env["ncollection.workspace.config"]
        cls.Menu = cls.env["ir.ui.menu"]
        # Menus without actions and without actionable descendants are pruned
        # by Odoo's own _visible_menu_ids ("empty folder" rule) — synthetic
        # menus need a real action to be visible at all.
        cls.action = cls.env["ir.actions.act_window"].create({
            "name": "Fake Things", "res_model": "res.partner",
            "view_mode": "list,form",
        })
        # Synthetic licensed-app menu tree: fake_app.menu_root > child
        cls.fake_root = cls.Menu.create({"name": "Fake App"})
        cls.fake_child = cls.Menu.create({
            "name": "Fake App / Things", "parent_id": cls.fake_root.id,
            "action": f"ir.actions.act_window,{cls.action.id}",
        })
        cls.env["ir.model.data"].create({
            "name": "menu_root", "module": "fake_app",
            "model": "ir.ui.menu", "res_id": cls.fake_root.id,
        })
        cls.env["ir.model.data"].create({
            "name": "menu_child", "module": "fake_app",
            "model": "ir.ui.menu", "res_id": cls.fake_child.id,
        })
        # A user-created root menu with NO xml-id: must never be blocked.
        cls.custom_root = cls.Menu.create({
            "name": "My Custom Menu",
            "action": f"ir.actions.act_window,{cls.action.id}",
        })

    def test_signature_pinned(self):
        """The Odoo 19 signature assertion must hold on this codebase."""
        self.assertTrue(menu_mod.SIGNATURE_OK)

    def test_no_config_fail_open(self):
        self.assertEqual(self.Menu._ncollection_blocked_module_names(), set())
        self.assertIn(self.fake_root.id, self.Menu._visible_menu_ids())

    def test_empty_allowed_list_fail_open(self):
        self.Config.create({"plan_code": "TRIAL"})
        self.assertEqual(self.Menu._ncollection_blocked_module_names(), set())

    def test_starter_blocks_unlicensed_app(self):
        """Starter (crm,sale,account): fake_app is not licensed -> blocked."""
        self.Config.create({"allowed_module_names": "crm,sale,account"})
        blocked_modules = self.Menu._ncollection_blocked_module_names()
        self.assertIn("fake_app", blocked_modules)
        blocked_ids = self.Menu._ncollection_blocked_menu_ids()
        self.assertIn(self.fake_root.id, blocked_ids)
        self.assertIn(self.fake_child.id, blocked_ids, "descendants blocked via parent_path")
        visible = self.Menu._visible_menu_ids()
        self.assertNotIn(self.fake_root.id, visible)
        self.assertNotIn(self.fake_child.id, visible)

    def test_enterprise_allows_licensed_app(self):
        """Enterprise (fake_app licensed): nothing of fake_app is blocked."""
        self.Config.create({"allowed_module_names": "crm,sale,account,fake_app"})
        self.assertNotIn("fake_app", self.Menu._ncollection_blocked_module_names())
        self.assertIn(self.fake_root.id, self.Menu._visible_menu_ids())
        self.assertIn(self.fake_child.id, self.Menu._visible_menu_ids())

    def test_whitelist_never_blocked(self):
        self.Config.create({"allowed_module_names": "crm"})
        blocked = self.Menu._ncollection_blocked_module_names()
        for name in ("base", "web", "mail", "bus"):
            self.assertNotIn(name, blocked)
        self.assertFalse(
            {m for m in blocked if m.startswith("ncollection_")},
            "ncollection_* modules must never be blocked",
        )

    def test_user_created_menu_never_blocked(self):
        self.Config.create({"allowed_module_names": "crm"})
        self.assertIn(self.custom_root.id, self.Menu._visible_menu_ids())

    def test_injected_blocked_set(self):
        """_ncollection_blocked_menu_ids accepts an injected module set."""
        blocked_ids = self.Menu._ncollection_blocked_menu_ids({"fake_app"})
        self.assertIn(self.fake_root.id, blocked_ids)
        self.assertIn(self.fake_child.id, blocked_ids)
        self.assertNotIn(self.custom_root.id, blocked_ids)

    def test_config_change_updates_menus(self):
        """Acceptance: changing workspace config updates menus (cache clear)."""
        cfg = self.Config.create({"allowed_module_names": "crm,sale,account"})
        self.assertNotIn(self.fake_root.id, self.Menu._visible_menu_ids())
        cfg.write({"allowed_module_names": "crm,sale,account,fake_app"})
        # write() cleared the registry cache; recompute must see the change
        self.assertIn(self.fake_root.id, self.Menu._visible_menu_ids())
        cfg.write({"allowed_module_names": "crm"})
        self.assertNotIn(self.fake_root.id, self.Menu._visible_menu_ids())
