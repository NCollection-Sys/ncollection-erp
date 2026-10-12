# -*- coding: utf-8 -*-
"""Applications: entitled (platform) / installed (platform) / visible (Owner).

The three facts must stay separate: hiding an app on Home removes a tile and NOTHING else; the Owner can never
entitle, install or list an application themselves; the status board reports each application's real state.
"""
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestLauncherApps(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Config = cls.env['ncollection.workspace.config']
        cls.Apps = cls.env['ncollection.launcher.app']
        cls.Menu = cls.env['ir.ui.menu']
        cls.owner = cls._user('apps_owner', 'ncollection_core.group_role_owner')
        cls.clerk = cls._user('apps_clerk', 'ncollection_core.group_role_employee')
        action = cls.env['ir.actions.act_window'].create({
            'name': 'Launcher Things', 'res_model': 'res.partner', 'view_mode': 'list,form'})
        cls.roots = {}
        for module, label in (('lic_app', 'Licensed App'), ('other_app', 'Other App')):
            root = cls.Menu.create({'name': label, 'action': 'ir.actions.act_window,%s' % action.id})
            cls.env['ir.model.data'].create({
                'name': 'menu_root', 'module': module, 'model': 'ir.ui.menu', 'res_id': root.id})
            cls.roots[module] = root

    @classmethod
    def _user(cls, login, role):
        return cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': login, 'login': login,
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id, cls.env.ref(role).id])]})

    def _row(self, module):
        return self.Apps.sudo().search([('module_name', '=', module)])

    def _launcher_names(self, user=None):
        return {a['name'] for a in (self.Menu.with_user(user) if user else self.Menu).nc_tenant_apps()}

    # ---------------------------------------------------------------- the status board
    def test_status_reports_entitled_installed_and_not_entitled(self):
        """active = in plan AND installed; pending = in plan, not yet installed (the platform installs it);
        locked = not in the plan. Uses real modules so the states are the database's own, not invented."""
        installed = self.env['ir.module.module'].sudo().search(
            [('name', '=', 'stock'), ('state', '=', 'installed'), ('application', '=', True)])
        self.assertTrue(installed, "stock is the installed Community application this test stands on")
        pending = self.env['ir.module.module'].sudo().search(
            [('name', '=', 'fleet'), ('application', '=', True), ('state', '=', 'uninstalled')])
        self.assertTrue(pending, "fleet is a Community application that must be available to NCollection")
        self.Config.create({'plan_code': 'T', 'allowed_module_names': 'stock,fleet'})
        self.Apps._nc_sync()
        self.assertEqual(self._row('stock').state, 'active')
        self.assertEqual(self._row('fleet').state, 'pending')
        self.assertEqual(self._row('mrp').state, 'locked')      # a Community app the plan does not include

    def test_the_seven_community_applications_are_listed_and_never_enterprise(self):
        self.Apps._nc_sync()
        listed = set(self.Apps.sudo().search([]).mapped('module_name'))
        for name in ('point_of_sale', 'mrp', 'hr_recruitment', 'fleet', 'im_livechat', 'hr_attendance',
                     'mass_mailing_sms'):
            self.assertIn(name, listed, name)
        # an Enterprise stub is not an application this database can offer
        self.assertNotIn('stock_barcode', listed)
        self.assertNotIn('account_accountant', listed)

    def test_platform_and_core_modules_are_never_customer_applications(self):
        self.Apps._nc_sync()
        listed = set(self.Apps.sudo().search([]).mapped('module_name'))
        self.assertFalse(listed & {'base', 'ncollection_core', 'ncollection_branding', 'ncollection_saas',
                                   'ncollection_subscription'})
        self.assertFalse([n for n in listed if n.startswith('ncollection_')])

    def test_sync_is_idempotent_and_keeps_preferences(self):
        self.Apps._nc_sync()
        row = self.Apps.sudo().search([], limit=1)
        row.write({'visible': False, 'sequence': 3})
        count = self.Apps.sudo().search_count([])
        self.Apps._nc_sync()
        self.assertEqual(self.Apps.sudo().search_count([]), count)
        self.assertFalse(row.visible)
        self.assertEqual(row.sequence, 3)

    # ---------------------------------------------------------------- who may change what
    def test_the_owner_changes_visibility_and_order_only(self):
        self.Apps._nc_sync()
        row = self.Apps.sudo().search([('state', '!=', 'locked')], limit=1) or self.Apps.sudo().search([], limit=1)
        as_owner = row.with_user(self.owner)
        as_owner.write({'visible': False, 'sequence': 2})
        self.assertFalse(row.visible)
        for forbidden in ({'state': 'active'}, {'module_name': 'x'}):
            with self.assertRaises(AccessError):
                as_owner.write(forbidden)
        with self.assertRaises(AccessError):
            self.Apps.with_user(self.owner).create({'module_name': 'crm', 'state': 'active'})
        with self.assertRaises(AccessError):
            as_owner.unlink()

    def test_nobody_else_can_read_or_change_the_list_but_the_launcher_still_works_for_them(self):
        self.Apps._nc_sync()
        row = self.Apps.sudo().search([], limit=1)
        with self.assertRaises(AccessError):
            row.with_user(self.clerk).read(['visible'])
        with self.assertRaises(AccessError):
            row.with_user(self.clerk).write({'visible': False})
        self.assertIsInstance(self.Menu.with_user(self.clerk).nc_tenant_apps(), list)

    def test_the_entitlement_cannot_be_changed_from_this_screen(self):
        """Installing stays the platform's: the #459 lockout is untouched, and this screen offers no install."""
        self.Apps._nc_sync()
        with self.assertRaises(AccessError):
            self.env['ir.module.module'].with_user(self.owner).search(
                [('name', '=', 'fleet')], limit=1).button_immediate_install()

    # ---------------------------------------------------------------- the launcher
    def test_hiding_an_app_removes_its_tile_and_nothing_else(self):
        self.Config.create({'plan_code': 'T', 'allowed_module_names': 'lic_app,other_app'})
        self.env.registry.clear_cache()
        self.assertEqual({'Licensed App', 'Other App'} - self._launcher_names(), set())
        self.Apps.sudo().create({'module_name': 'lic_app', 'state': 'active', 'visible': False})
        names = self._launcher_names()
        self.assertNotIn('Licensed App', names)                     # hidden on Home ...
        self.assertIn('Other App', names)                           # ... and only that one
        # ... but the menu itself, the licensing and the access are untouched
        self.assertIn(self.roots['lic_app'].id, self.Menu._visible_menu_ids())
        self.assertNotIn('lic_app', self.Menu._ncollection_blocked_module_names())

    def test_visibility_grants_nothing(self):
        """A tile for an app the plan does not license never appears, whatever the preference says."""
        self.Config.create({'plan_code': 'T', 'allowed_module_names': 'lic_app'})
        self.env.registry.clear_cache()
        self.Apps.sudo().create({'module_name': 'other_app', 'state': 'locked', 'visible': True})
        self.assertNotIn('Other App', self._launcher_names())

    def test_the_owner_order_comes_first_and_the_rest_keep_their_natural_order(self):
        self.Config.create({'plan_code': 'T', 'allowed_module_names': 'lic_app,other_app'})
        self.env.registry.clear_cache()
        self.Apps.sudo().create({'module_name': 'other_app', 'state': 'active', 'sequence': 1})
        ordered = [a['name'] for a in self.Menu.nc_tenant_apps()]
        self.assertEqual(ordered[0], 'Other App')
        self.assertIn('Licensed App', ordered[1:])
