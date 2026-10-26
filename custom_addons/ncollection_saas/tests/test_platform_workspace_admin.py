# -*- coding: utf-8 -*-
"""Workspace Administration (and so Print Templates) reaches the platform's administrators, without the Owner role."""
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestPlatformWorkspaceAdmin(TransactionCase):

    def test_a_platform_administrator_sees_workspace_administration_and_print_templates(self):
        root = self.env.ref('ncollection_core.menu_workspace_settings_root')
        owner = self.env.ref('ncollection_core.group_role_owner')
        platform = self.env.ref('ncollection_subscription.group_platform_admin')
        self.assertIn(owner, root.group_ids, 'the tenant Owner keeps it')
        self.assertIn(platform, root.group_ids)
        staff = self.env['res.users'].create({
            'name': 'Platform Staff WA', 'login': 'plat_staff_wa',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id, platform.id])]})
        self.assertFalse(staff.has_group('ncollection_core.group_role_owner'), 'no Owner role is given')
        visible = self.env['ir.ui.menu'].with_user(staff)._visible_menu_ids()
        self.assertIn(root.id, visible)
        self.assertIn(self.env.ref('ncollection_core.menu_workspace_print_settings').id, visible)
        clerk = self.env['res.users'].create({'name': 'Clerk WA', 'login': 'plat_clerk_wa',
                                              'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        self.assertNotIn(root.id, self.env['ir.ui.menu'].with_user(clerk)._visible_menu_ids())
