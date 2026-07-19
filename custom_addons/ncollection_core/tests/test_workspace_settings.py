# -*- coding: utf-8 -*-
"""Owner workspace settings & user management (P1-T12)."""

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestWorkspaceSettings(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Config = cls.env["ncollection.workspace.config"]
        cls.Invite = cls.env["ncollection.user.invite"]
        cls.owner = new_test_user(cls.env, login="ws_owner", groups="base.group_user")
        cls.owner.write({
            "group_ids": [(4, cls.env.ref("ncollection_core.group_role_owner").id)],
        })
        cls.sales = new_test_user(cls.env, login="ws_sales", groups="base.group_user")
        cls.sales.write({
            "group_ids": [(4, cls.env.ref("ncollection_core.group_role_sales").id)],
        })

    def _active_internal_count(self):
        return self.env["res.users"].sudo().search_count([
            ("active", "=", True), ("share", "=", False),
        ])

    # ---------------- invite ----------------

    def test_owner_invites_user_with_role(self):
        wizard = self.Invite.with_user(self.owner).create({
            "name": "New Hire", "email": "hire@example.com", "role": "warehouse",
        })
        wizard.action_invite()
        user = self.env["res.users"].sudo().search([("login", "=", "hire@example.com")])
        self.assertTrue(user)
        self.assertIn(
            self.env.ref("ncollection_core.group_role_warehouse"),
            user.group_ids,
        )
        self.assertIn(self.env.ref("base.group_user"), user.all_group_ids)

    def test_invite_never_exposes_groups(self):
        """The wizard's fields are name/email/role only — no group m2m."""
        fields = self.Invite.fields_get()
        self.assertNotIn("group_ids", fields)
        self.assertEqual(
            sorted(f for f in fields if not f.startswith(("create_", "write_", "id", "display_"))),
            ["email", "name", "role"],
        )

    def test_non_owner_denied_wizard(self):
        """ACL mirror: Sales cannot even create the invite wizard."""
        with self.assertRaises(AccessError):
            self.Invite.with_user(self.sales).create({
                "name": "X", "email": "x@example.com", "role": "employee",
            })

    def test_invalid_email_rejected(self):
        with self.assertRaises(ValidationError):
            self.Invite.with_user(self.owner).create({
                "name": "X", "email": "not-an-email", "role": "employee",
            })

    # ---------------- max-user limit ----------------

    def test_limit_blocks_invite_with_upgrade_message(self):
        self.Config.create({"max_users": self._active_internal_count()})
        wizard = self.Invite.with_user(self.owner).create({
            "name": "Over Limit", "email": "over@example.com", "role": "employee",
        })
        with self.assertRaises(ValidationError) as cm:
            wizard.action_invite()
        self.assertIn("Upgrade", str(cm.exception))

    def test_limit_blocks_raw_orm_create(self):
        """Rule 4: the real control is in res.users.create, not just the UI."""
        self.Config.create({"max_users": self._active_internal_count()})
        with self.assertRaises(ValidationError):
            self.env["res.users"].with_user(self.owner).create({
                "name": "Bypass Attempt", "login": "bypass@example.com",
                "group_ids": [(4, self.env.ref("base.group_user").id)],
            })

    def test_limit_zero_means_unlimited(self):
        self.Config.create({"max_users": 0})
        wizard = self.Invite.with_user(self.owner).create({
            "name": "Free Seat", "email": "free@example.com", "role": "employee",
        })
        wizard.action_invite()  # must not raise
        self.assertTrue(
            self.env["res.users"].sudo().search([("login", "=", "free@example.com")])
        )

    def test_reactivation_counts_against_limit(self):
        victim = new_test_user(self.env, login="ws_victim", groups="base.group_user")
        victim.with_user(self.owner).action_ncollection_deactivate()
        self.Config.create({"max_users": self._active_internal_count()})
        with self.assertRaises(ValidationError):
            victim.sudo().with_user(self.owner).write({"active": True})

    # ---------------- deactivate ----------------

    def test_owner_deactivates_user(self):
        victim = new_test_user(self.env, login="ws_bye", groups="base.group_user")
        victim.with_user(self.owner).action_ncollection_deactivate()
        self.assertFalse(victim.active)

    def test_self_deactivation_blocked(self):
        with self.assertRaises(ValidationError):
            self.owner.with_user(self.owner).action_ncollection_deactivate()

    def test_non_owner_deactivate_denied(self):
        """ORM/RPC mirror: Sales calling the method directly is denied."""
        victim = new_test_user(self.env, login="ws_safe", groups="base.group_user")
        with self.assertRaises(AccessError):
            victim.with_user(self.sales).action_ncollection_deactivate()

    def test_last_owner_protected(self):
        # our test owner + possibly others; deactivate all but one, then block
        owner_group = self.env.ref("ncollection_core.group_role_owner")
        owners = self.env["res.users"].sudo().search([
            ("active", "=", True), ("share", "=", False),
            ("all_group_ids", "in", owner_group.id),
        ])
        if len(owners) > 1:
            (owners - self.owner).sudo().write({"active": False})
        with self.assertRaises(ValidationError):
            self.owner.sudo().action_ncollection_deactivate()

    # ---------------- menu visibility ----------------

    def test_menu_owner_only(self):
        menu = self.env.ref("ncollection_core.menu_workspace_settings_root")
        visible_owner = self.env["ir.ui.menu"].with_user(self.owner)._visible_menu_ids()
        visible_sales = self.env["ir.ui.menu"].with_user(self.sales)._visible_menu_ids()
        self.assertIn(menu.id, visible_owner)
        self.assertNotIn(menu.id, visible_sales)
