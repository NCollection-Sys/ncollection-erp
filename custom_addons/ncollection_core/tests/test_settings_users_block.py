# -*- coding: utf-8 -*-
"""Request 01: a tenant's General Settings no longer carries Odoo's "Users" block (the Workspace Administration Center
owns users and invitations); the platform database keeps it; the user administration behind it is untouched."""
from lxml import etree

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestSettingsUsersBlock(TransactionCase):

    def _block(self):
        arch = self.env['res.config.settings'].get_view(
            self.env.ref('base_setup.res_config_settings_view_form').id, 'form')['arch']
        nodes = etree.fromstring(arch).xpath("//block[@name='users_setting_container']")
        self.assertEqual(len(nodes), 1, 'the block is hidden, never deleted from the arch')
        return nodes[0]

    def test_hidden_in_a_tenant_shown_on_the_platform(self):
        platform = bool(self.env['ir.module.module'].search_count(
            [('name', '=', 'ncollection_saas'), ('state', '=', 'installed')]))
        hidden = self._block().get('invisible') in ('1', 'True', 'true')
        self.assertEqual(hidden, not platform)

    def test_user_administration_itself_is_untouched(self):
        self.assertTrue(self.env.ref('base.action_res_users'))
        self.assertTrue(self.env.ref('ncollection_core.view_workspace_company_form'))
