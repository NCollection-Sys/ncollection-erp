# -*- coding: utf-8 -*-
"""Owner workspace settings & user management (P1-T12)."""

import secrets

from odoo.exceptions import AccessError, UserError, ValidationError
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

    # ---------------- New User (the wizard keeps its technical name, ncollection.user.invite) ----------------

    def _role(self, code):
        return self.env["ncollection.role"].sudo().search([("code", "=", code)], limit=1)

    def _new(self, user=None, **vals):
        values = {"name": "New Hire", "username": "newhire", "email": "hire@example.com",
                  "role_id": self._role("sales_user").id}
        values.update(vals)
        return self.Invite.with_user(user or self.owner).create(values)

    def test_owner_creates_a_user_with_a_role_ready_to_sign_in(self):
        result = self._new().action_create_user()
        user = self.env["res.users"].sudo().search([("login", "=", "newhire")])
        self.assertTrue(user and user.active and not user.share)
        self.assertIn(self._role("sales_user"), user.nc_role_ids)
        self.assertTrue(self._role("sales_user").group_ids <= user.all_group_ids, "the role is its real groups")
        self.assertTrue(user.nc_must_change_password, "a temporary password must be changed at first sign-in")
        message = result["params"]["message"]
        self.assertIn("newhire", message)
        self.assertTrue(result["params"]["sticky"], "the temporary password is shown once, until dismissed")
        # usable now: the shown password authenticates (no invitation e-mail, no link)
        temporary = message.split("temporary password ", 1)[1].split()[0]
        user.with_user(user)._check_credentials({"type": "password", "password": temporary}, {"interactive": True})

    def test_a_typed_password_is_used_and_never_kept_on_the_wizard(self):
        typed = "Aa1-" + secrets.token_urlsafe(12)
        wizard = self._new(username="typed.pw", mode="explicit", new_password=typed, must_change=False)
        wizard.action_create_user()
        self.assertFalse(wizard.new_password)
        user = self.env["res.users"].sudo().search([("login", "=", "typed.pw")])
        self.assertFalse(user.nc_must_change_password)
        user.with_user(user)._check_credentials({"type": "password", "password": typed},
                                                {"interactive": True})

    def test_more_roles_are_added_to_the_role(self):
        self._new(username="two.roles", role_ids=[(6, 0, self._role("purchase_user").ids)]).action_create_user()
        user = self.env["res.users"].sudo().search([("login", "=", "two.roles")])
        self.assertEqual(user.nc_role_ids & (self._role("sales_user") | self._role("purchase_user")),
                         self._role("sales_user") | self._role("purchase_user"))

    def test_the_wizard_offers_only_the_roles_this_workspace_has(self):
        wizard = self._new()
        self.assertTrue(wizard.available_role_ids)
        self.assertFalse(wizard.available_role_ids.filtered(lambda role: not role.available))

    def test_the_wizard_never_exposes_groups(self):
        """Name, username, email, role(s) and the password choice only: roles are names, never a group m2m."""
        fields = self.Invite.fields_get()
        self.assertNotIn("group_ids", fields)
        self.assertEqual(
            sorted(f for f in fields if not f.startswith(("create_", "write_", "id", "display_"))),
            ["available_role_ids", "email", "mode", "must_change", "name", "new_password", "role_id", "role_ids",
             "username"],
        )

    def test_non_owner_denied_wizard(self):
        """ACL mirror: Sales cannot even create the wizard."""
        with self.assertRaises(AccessError):
            self._new(user=self.sales, username="xuser")

    def test_a_non_administrator_cannot_hand_out_administration(self):
        """Escalation is refused by the role service itself (_nc_apply_roles), whatever the screen: a user manager
        who is not an administrator cannot create an Administrator."""
        manager = new_test_user(self.env, login="ws_usermgr", groups="base.group_user,base.group_erp_manager")
        with self.assertRaises(UserError):     # AccessError and ValidationError are UserErrors too
            self.env["res.users"].with_user(manager)._nc_create_workspace_user(
                "Escalated", "escalated", role_ids=self._role("administrator").ids)
        self.assertFalse(self.env["res.users"].sudo().with_context(active_test=False).search(
            [("login", "=", "escalated")]))

    def test_invalid_email_rejected(self):
        with self.assertRaises(ValidationError):
            self._new(username="xuser", email="not-an-email")

    # ---------------- max-user limit ----------------

    def test_limit_blocks_a_new_user(self):
        self.Config.create({"max_users": self._active_internal_count()})
        wizard = self._new(username="overlimit")
        with self.assertRaises(ValidationError):
            wizard.action_create_user()
        self.assertFalse(self.env["res.users"].sudo().search([("login", "=", "overlimit")]))

    def test_limit_blocks_raw_orm_create(self):
        """Rule 4: the real control is in res.users.create, not just the UI."""
        self.Config.create({"max_users": self._active_internal_count()})
        with self.assertRaises(ValidationError):
            self.env["res.users"].with_user(self.owner).create({
                "name": "Bypass Attempt", "login": "bypassuser",
                "group_ids": [(4, self.env.ref("base.group_user").id)],
            })

    def test_limit_zero_means_unlimited(self):
        self.Config.create({"max_users": 0})
        self._new(username="freeseat").action_create_user()  # must not raise
        self.assertTrue(self.env["res.users"].sudo().search([("login", "=", "freeseat")]))

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

    def test_users_has_new_user_and_no_invite_menu(self):
        self.assertFalse(self.env.ref("ncollection_core.menu_workspace_invite", raise_if_not_found=False))
        arch = self.env.ref("ncollection_core.view_workspace_users_list").arch
        action = self.env.ref("ncollection_core.action_user_invite")
        self.assertIn('name="%d"' % action.id, arch, "the list's Add User button opens the Add User form")
        self.assertEqual(action.name, "Add User")

    # ---------------- usernames, not email addresses ----------------

    def test_the_user_signs_in_with_the_username_not_the_email(self):
        self._new(username="sara.m", email="sara@example.com").action_create_user()
        user = self.env["res.users"].sudo().search([("login", "=", "sara.m")])
        self.assertEqual(user.email, "sara@example.com")      # contact detail only
        self.assertFalse(self.env["res.users"].sudo().search(
            [("login", "=", "sara@example.com")]))

    def test_an_email_is_refused_as_the_username(self):
        with self.assertRaises(ValidationError):
            self._new(username="x@example.com", email="x@example.com")

    def test_a_taken_username_is_refused_before_anything_is_created(self):
        self._new(name="A", username="dupuser", email="a@example.com").action_create_user()
        wizard = self._new(name="B", username="dupuser", email="b@example.com")
        with self.assertRaises(ValidationError):
            wizard.action_create_user()
        self.assertEqual(self.env["res.users"].sudo().search_count([("login", "=", "dupuser")]), 1)

    def test_the_orm_refuses_an_email_login_for_a_human_user(self):
        """Rule 4: the wizard is not the control, res.users is."""
        Users = self.env["res.users"].with_context(no_reset_password=True)
        group = [(6, 0, [self.env.ref("base.group_user").id])]
        with self.assertRaises(ValidationError):
            Users.create({"name": "E", "login": "e@example.com", "group_ids": group})
        user = Users.create({"name": "U", "login": "plainuser", "group_ids": group})
        with self.assertRaises(ValidationError):
            user.login = "plainuser@example.com"

    def test_portal_customers_and_service_users_keep_email_style_logins(self):
        """The rule is about the people who run the ERP. A portal customer signs
        in with their email; the cron and config-sync service accounts are
        addressed by internal-domain logins."""
        Users = self.env["res.users"].with_context(no_reset_password=True)
        portal = Users.create({
            "name": "Customer", "login": "customer@example.com",
            "group_ids": [(6, 0, [self.env.ref("base.group_portal").id])]})
        self.assertTrue(portal)
        service = Users.create({
            "name": "Svc", "login": "svc-test@ncollection.internal",
            "group_ids": [(6, 0, [self.env.ref("base.group_user").id])]})
        self.assertTrue(service)
