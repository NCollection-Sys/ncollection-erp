# -*- coding: utf-8 -*-
"""The platform administrator reaches Settings from Home.

The platform database never assigns the tenant Owner role, and core's
owner-only filter used to hide Settings from everyone else, so the person who
runs the platform had no Settings card. The exemption is by platform-admin
group; a platform admin without system rights, and every ordinary user, are
unchanged.
"""
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestPlatformSettingsCard(TransactionCase):

    def _user(self, login, *xmlids):
        groups = [self.env.ref('base.group_user').id] + [self.env.ref(x).id for x in xmlids]
        return self.env['res.users'].with_context(no_reset_password=True).create({
            'name': login, 'login': login, 'group_ids': [(6, 0, groups)]})

    def _cards(self, user):
        return {a['xmlid'] for a in self.env['ir.ui.menu'].with_user(user).nc_tenant_apps()}

    def test_platform_admin_sees_and_can_open_settings(self):
        admin = self._user('plat_admin_x', 'base.group_system',
                           'ncollection_subscription.group_platform_admin')
        self.assertIn('base.menu_administration', self._cards(admin))
        self.env['res.config.settings'].with_user(admin).create({})

    def test_an_ordinary_employee_gets_neither_card_nor_access(self):
        clerk = self._user('plat_clerk_x')
        self.assertNotIn('base.menu_administration', self._cards(clerk))
        with self.assertRaises(AccessError):
            self.env['res.config.settings'].with_user(clerk).create({})

    def test_a_platform_admin_without_system_rights_gets_no_card(self):
        """The exemption only lifts the menu filter; it never grants Settings."""
        user = self._user('plat_only_x', 'ncollection_subscription.group_platform_admin')
        self.assertNotIn('base.menu_administration', self._cards(user))
