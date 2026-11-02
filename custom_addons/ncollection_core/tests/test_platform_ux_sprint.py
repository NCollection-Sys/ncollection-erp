# -*- coding: utf-8 -*-
"""Apps belong to the platform (SEC-01), developer mode for the people who may use it (DEV-01), and the contact form's
Website placeholder (CONTACT-01)."""
from unittest.mock import patch

from lxml import etree

from odoo import api
from odoo.exceptions import AccessError
from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged

PLATFORM_GROUP = 'ncollection_subscription.group_platform_admin'


def _as_tenant(test):
    """Model a TENANT database, where the platform administrator group does not exist (the same patch as
    test_module_management_lockout)."""
    original = api.Environment.ref

    def ref(env, xmlid, raise_if_not_found=True):
        if xmlid == PLATFORM_GROUP and not raise_if_not_found:
            return env['res.groups']
        return original(env, xmlid, raise_if_not_found)
    patcher = patch.object(api.Environment, 'ref', ref)
    patcher.start()
    test.addCleanup(patcher.stop)


@tagged('post_install', '-at_install')
class TestAppsBelongToThePlatform(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.apps = cls.env.ref('base.menu_management')
        cls.settings = cls.env.ref('base.menu_administration')
        cls.owner = new_test_user(cls.env, login='ux_owner',
                                  groups='base.group_user,ncollection_core.group_role_owner')
        cls.clerk = new_test_user(cls.env, login='ux_clerk', groups='base.group_user')

    def _visible(self, user):
        return self.env['ir.ui.menu'].with_user(user)._visible_menu_ids()

    def test_a_tenant_owner_keeps_settings_but_no_longer_sees_apps(self):
        _as_tenant(self)
        visible = self._visible(self.owner)
        self.assertIn(self.settings.id, visible, "the Owner still administers the workspace")
        self.assertNotIn(self.apps.id, visible)
        self.assertNotIn(self.apps.id, self._visible(self.clerk))

    def test_the_apps_screen_is_refused_to_a_tenant_owner_reaching_it_by_url(self):
        _as_tenant(self)
        Module = self.env['ir.module.module'].with_user(self.owner)
        with self.assertRaises(AccessError):
            Module.web_search_read([], {'name': {}})
        with self.assertRaises(AccessError):
            Module.search([], limit=1).web_read({'name': {}})
        self.assertTrue(Module.search_count([]), "server code that reads modules still can")

    def test_refreshing_the_list_and_applying_scheduled_upgrades_are_refused_too(self):
        _as_tenant(self)
        for model, method in (('base.module.update', 'update_module'), ('base.module.upgrade', 'upgrade_module'),
                              ('base.module.upgrade', 'upgrade_module_cancel')):
            with self.subTest(model=model, method=method), self.assertRaises(AccessError):
                getattr(self.env[model].with_user(self.owner).create({}), method)()
        with self.assertRaises(AccessError):
            self.env['ir.module.module'].with_user(self.owner).update_list()

    def test_installed_business_apps_stay_visible(self):
        _as_tenant(self)
        contacts = self.env.ref('contacts.menu_contacts', raise_if_not_found=False)
        if contacts:
            self.assertIn(contacts.id, self._visible(self.owner))

    def test_the_superuser_and_a_platform_administrator_keep_apps(self):
        self.assertIn(self.apps.id, self.env['ir.ui.menu'].sudo()._visible_menu_ids())
        group = self.env.ref(PLATFORM_GROUP, raise_if_not_found=False)
        if group:   # the platform database (ncollection_subscription installed)
            admin = new_test_user(self.env, login='ux_platform', groups='base.group_user,base.group_system')
            admin.group_ids |= group
            self.assertIn(self.apps.id, self._visible(admin))
            self.assertTrue(self.env['ir.module.module'].with_user(admin).web_search_read([], {'name': {}}))


@tagged('post_install', '-at_install')
class TestDeveloperMode(HttpCase):
    """Settings > Activate the developer mode is Odoo's own `?debug=1`; the gate keeps it for the tenant Owner and the
    platform administrator, and strips it for everyone else (tenant users must not reach the technical menus)."""

    def _page_debug(self, url):
        """The debug mode the web client page actually renders with (`debug: "..."` in its bootstrap script)."""
        text = self.url_open(url).text
        marker = 'debug: "'
        start = text.find(marker)
        self.assertGreater(start, -1, "the web client page rendered")
        return text[start + len(marker):text.find('"', start + len(marker))]

    def _debug_after_activation(self, login):
        self.authenticate(login, login)
        return self._page_debug('/odoo?debug=1')

    def test_an_ordinary_user_cannot_switch_it_on(self):
        new_test_user(self.env, login='dev_clerk', groups='base.group_user')
        self.assertFalse(self._debug_after_activation('dev_clerk'))

    def test_the_workspace_owner_can(self):
        new_test_user(self.env, login='dev_owner',
                      groups='base.group_user,ncollection_core.group_role_owner')
        self.assertEqual(self._debug_after_activation('dev_owner'), '1')

    def test_the_platform_administrator_can(self):
        group = self.env.ref(PLATFORM_GROUP, raise_if_not_found=False)
        if not group:
            return  # not the platform database: the case cannot arise here
        admin = new_test_user(self.env, login='dev_platform',
                              groups='base.group_user,base.group_system')
        admin.group_ids |= group
        self.assertEqual(self._debug_after_activation('dev_platform'), '1')

    def test_it_switches_off_the_normal_way(self):
        new_test_user(self.env, login='dev_off',
                      groups='base.group_user,ncollection_core.group_role_owner')
        self.assertEqual(self._debug_after_activation('dev_off'), '1')
        self.assertEqual(self._page_debug('/odoo/action-base.action_res_users'), '1', "it survives navigation")
        self.assertEqual(self._page_debug('/odoo?debug=0'), '')
        self.assertEqual(self._page_debug('/odoo'), '', "and stays off")


@tagged('post_install', '-at_install')
class TestContactWebsitePlaceholder(TransactionCase):

    def _placeholder(self):
        views = self.env['res.partner'].get_views([(False, 'form')])
        arch = etree.fromstring(views['views']['form']['arch'])
        return [f.get('placeholder') for f in arch.iter('field') if f.get('name') == 'website']

    def test_the_placeholder_names_the_configured_domain_and_follows_a_change(self):
        ICP = self.env['ir.config_parameter'].sudo()
        ICP.set_param('ncollection_saas.base_domain', 'balancecloud.ae')
        self.assertTrue(self._placeholder())
        self.assertTrue(all('balancecloud.ae' in p for p in self._placeholder()))
        ICP.set_param('ncollection_saas.base_domain', 'example-platform.com')
        self.assertTrue(all('example-platform.com' in p for p in self._placeholder()))

    def test_no_or_an_invalid_domain_keeps_odoos_placeholder(self):
        ICP = self.env['ir.config_parameter'].sudo()
        with patch.dict('os.environ', {'NC_BASE_DOMAIN': ''}):
            ICP.set_param('ncollection_saas.base_domain', False)
            self.assertTrue(all('odoo.com' in p for p in self._placeholder()))
            ICP.set_param('ncollection_saas.base_domain', 'x" onload="evil')
            self.assertTrue(all('evil' not in p for p in self._placeholder()))

    def test_it_is_a_placeholder_never_a_value(self):
        self.env['ir.config_parameter'].sudo().set_param('ncollection_saas.base_domain', 'balancecloud.ae')
        partner = self.env['res.partner'].create({'name': 'Placeholder Probe'})
        self.assertFalse(partner.website)
        self.assertFalse(self.env['res.partner'].default_get(['website']).get('website'))


@tagged('post_install', '-at_install')
class TestBecomeSuperuserBelongsToThePlatform(HttpCase):
    """"Become Superuser" (/web/become, shown in the developer menu) turned a tenant Owner into the superuser, and
    module installation trusts the superuser: developer mode must not be a way around the Apps restriction."""

    def _uid_after_become(self, login):
        self.authenticate(login, login)
        self.url_open('/web/become')
        return self.make_jsonrpc_request('/web/session/get_session_info', {})['uid']

    def test_a_tenant_owner_stays_who_they_are(self):
        _as_tenant(self)
        owner = new_test_user(self.env, login='become_owner',
                              groups='base.group_user,ncollection_core.group_role_owner')
        self.assertTrue(owner.has_group('base.group_system'), "the probe really is a system user")
        self.assertEqual(self._uid_after_become('become_owner'), owner.id)

    def test_the_platform_administrator_keeps_it(self):
        group = self.env.ref(PLATFORM_GROUP, raise_if_not_found=False)
        if not group:
            return  # not the platform database: the case cannot arise here
        admin = new_test_user(self.env, login='become_platform',
                              groups='base.group_user,base.group_system')
        admin.group_ids |= group
        self.assertEqual(self._uid_after_become('become_platform'), 1)
