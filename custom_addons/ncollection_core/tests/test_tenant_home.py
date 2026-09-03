# -*- coding: utf-8 -*-
"""Tenant application launcher — the app grid is DERIVED, not declared (#455).

The launcher's only claim is that what it shows equals what the tenant is
actually licensed and permitted to see. These tests attack that claim from
both directions, because only asserting the happy direction would pass just as
well on a launcher that shows everything:

  * a module the plan does NOT license must be absent, and
  * a module it DOES license must be present (the control — without it,
    "returns nothing at all" would look like perfect licensing).

Same synthetic-app technique as test_menu_visibility.py: a root menu whose
xml-id belongs to a made-up module, so nothing depends on which real Odoo apps
this database happens to have installed.
"""
import pathlib

from odoo.tests import TransactionCase, tagged

_HOME_JS = (pathlib.Path(__file__).resolve().parent.parent
            / 'static' / 'src' / 'home' / 'tenant_home.js')


@tagged('post_install', '-at_install')
class TestTenantHomeApps(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Config = cls.env['ncollection.workspace.config']
        cls.Menu = cls.env['ir.ui.menu']
        cls.action = cls.env['ir.actions.act_window'].create({
            'name': 'Launcher Things', 'res_model': 'res.partner',
            'view_mode': 'list,form',
        })

        def app(module, label):
            """A root menu owned by `module`, with an action so Odoo's own
            'empty folder' pruning keeps it visible."""
            root = cls.Menu.create({
                'name': label,
                'action': 'ir.actions.act_window,%s' % cls.action.id,
            })
            cls.env['ir.model.data'].create({
                'name': 'menu_root', 'module': module,
                'model': 'ir.ui.menu', 'res_id': root.id,
            })
            return root

        cls.licensed = app('lic_app', 'Licensed App')
        cls.unlicensed = app('unlic_app', 'Unlicensed App')

    def _app_names(self):
        return {a['name'] for a in self.Menu.nc_tenant_apps()}

    def _app_xmlids(self):
        return {a['xmlid'] for a in self.Menu.nc_tenant_apps()}

    # ------------------------------------------------------- licensing
    def test_the_plan_decides_which_apps_appear(self):
        """THE CLAIM, both directions at once. A plan that licenses one app and
        not the other must produce a launcher containing exactly the first —
        asserting only the absence would also pass if the method returned []."""
        self.Config.create({'plan_code': 'TEST', 'allowed_module_names': 'lic_app'})
        self.env.registry.clear_cache()

        names = self._app_names()
        self.assertIn('Licensed App', names,
                      "a licensed app must reach the launcher")
        self.assertNotIn('Unlicensed App', names,
                         "an app outside the plan must not reach the launcher")

    def test_relicensing_moves_the_launcher_with_no_launcher_side_change(self):
        """The point of deriving from menus: when the plan changes, the grid
        changes, and nothing in the launcher had to be told about it."""
        config = self.Config.create({
            'plan_code': 'TEST', 'allowed_module_names': 'lic_app'})
        self.env.registry.clear_cache()
        self.assertNotIn('Unlicensed App', self._app_names())

        # Exactly what config sync writes into a tenant when a plan is edited.
        config.allowed_module_names = 'lic_app,unlic_app'
        self.env.registry.clear_cache()
        self.assertIn('Unlicensed App', self._app_names(),
                      "adding a module to the plan must surface it on the home grid")

    # ------------------------------------------------------- exclusions
    def test_administration_and_dev_surfaces_are_not_customer_apps(self):
        """Apps/Settings/Tests and the internal component playground are not
        part of a customer's home grid, whatever else is licensed."""
        self.Config.create({'plan_code': 'TEST', 'allowed_module_names': 'lic_app'})
        self.env.registry.clear_cache()
        xmlids = self._app_xmlids()
        for excluded in ('base.menu_management', 'base.menu_administration',
                         'base.menu_tests',
                         'ncollection_branding.menu_component_playground'):
            self.assertNotIn(excluded, xmlids)

    def test_the_launcher_never_lists_itself(self):
        """A home screen that offers itself as one of its apps is a loop."""
        self.assertNotIn('ncollection_core.menu_ncollection_home_root',
                         self._app_xmlids())

    # ------------------------------------------------------- payload shape
    def test_each_app_carries_what_the_client_needs_and_no_module_list(self):
        """The client renders this verbatim, so the payload must be complete —
        and the icon must be the module's OWN web_icon data, never a
        substitute invented here."""
        self.Config.create({'plan_code': 'TEST', 'allowed_module_names': 'lic_app'})
        self.env.registry.clear_cache()
        apps = [a for a in self.Menu.nc_tenant_apps() if a['name'] == 'Licensed App']
        self.assertEqual(len(apps), 1)
        app = apps[0]
        for key in ('id', 'xmlid', 'name', 'action_id', 'web_icon',
                    'web_icon_data', 'sequence'):
            self.assertIn(key, app)
        self.assertEqual(app['action_id'], self.action.id,
                         "the card must open the menu's own action")

    # ------------------------------------------- the setup() crash (#457)
    def test_the_component_asks_for_no_service_that_does_not_exist(self):
        """THE #457 CRASH, PINNED.

        `setup()` called `useService("company")`. There is no `company` service
        in Odoo 19 — the current company is on `user.activeCompany` — so the
        component threw "Service company is not available" before its first
        render and EVERY tenant home load failed.

        Reading the source is crude, and it is the only check available: this
        repo has no JS test infrastructure, and #455's tests all exercised the
        server method (`nc_tenant_apps`), which is exactly why a broken
        `setup()` shipped with a green suite. A test that mounted the component
        would be better; until that infrastructure exists, this pins the
        specific mistake and its fix rather than pretending the gap is closed.
        """
        source = _HOME_JS.read_text(encoding='utf-8')
        self.assertNotIn(
            'useService("company")', source,
            "there is no `company` service in Odoo 19 — read the active "
            "company from user.activeCompany (@web/core/user) instead.")
        self.assertIn(
            'user.activeCompany', source,
            "the launcher must read the company from user.activeCompany, the "
            "source Odoo's own components use.")

    def test_the_launcher_does_not_swallow_its_own_errors(self):
        """#457 asked for the crash to be fixed, not hidden. A try/catch around
        setup() would turn the next missing dependency into a silently empty
        home page — the failure mode that is harder to diagnose, not easier."""
        source = _HOME_JS.read_text(encoding='utf-8')
        self.assertNotIn('try {', source,
                         "no try/catch in the launcher: a swallowed startup "
                         "error becomes a blank page with no explanation.")

    # ------------------------------------------------------- permissions
    def test_a_user_only_sees_apps_their_groups_allow(self):
        """Licensing is not the only filter: the launcher reads the CURRENT
        user's visible roots, so group restrictions apply too. Restricting the
        app to a group the probe user lacks must remove it from their grid
        while the licence still allows it."""
        self.Config.create({'plan_code': 'TEST', 'allowed_module_names': 'lic_app'})
        self.env.registry.clear_cache()

        probe = self.env['res.users'].create({
            'name': 'Launcher Probe', 'login': 'nc_launcher_probe',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])],
        })
        as_probe = self.Menu.with_user(probe)
        self.assertIn('Licensed App', {a['name'] for a in as_probe.nc_tenant_apps()},
                      "control: the probe user can see the app before it is restricted")

        self.licensed.group_ids = [(6, 0, [self.env.ref('base.group_system').id])]
        self.env.registry.clear_cache()
        self.assertNotIn('Licensed App', {a['name'] for a in as_probe.nc_tenant_apps()},
                         "a group the user lacks must remove the app from their grid")
