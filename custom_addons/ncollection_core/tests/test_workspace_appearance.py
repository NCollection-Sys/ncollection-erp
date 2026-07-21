# -*- coding: utf-8 -*-
"""Workspace Appearance page (P1-T16).

The Owner-facing branding page lives in ncollection_core (which owns the
Workspace Settings menu) and edits the res.company nc_* fields defined by
ncollection_branding. These tests assert the page is wired and Owner-gated,
and that the core -> branding dependency is in effect.
"""

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestWorkspaceAppearance(TransactionCase):

    def test_appearance_menu_nested_and_owner_gated(self):
        menu = self.env.ref("ncollection_core.menu_workspace_appearance")
        root = self.env.ref("ncollection_core.menu_workspace_settings_root")
        # nested under the P1-T12 Workspace Settings root (the integration hook)
        self.assertEqual(menu.parent_id, root)
        # Owner gating is inherited from the gated parent root menu
        owner = self.env.ref("ncollection_core.group_role_owner")
        self.assertIn(owner, root.group_ids,
                      "the settings root (parent of Appearance) must be Owner-gated")

    def test_appearance_action_opens_current_company(self):
        action = self.env.ref("ncollection_core.action_workspace_appearance")
        self.assertEqual(action.type, "ir.actions.server")
        self.assertEqual(action.model_id.model, "res.company")

    def test_appearance_view_exposes_branding_fields(self):
        view = self.env.ref("ncollection_core.view_workspace_appearance_form")
        self.assertEqual(view.model, "res.company")
        arch = str(view.arch)
        for fname in ("nc_primary_color", "nc_secondary_color",
                      "nc_sidebar_color", "nc_login_background"):
            self.assertIn(fname, arch)

    def test_core_depends_on_branding(self):
        """The dependency edge that lets core reference branding's fields."""
        core = self.env["ir.module.module"].search([("name", "=", "ncollection_core")])
        dep_names = core.dependencies_id.mapped("name")
        self.assertIn("ncollection_branding", dep_names)
