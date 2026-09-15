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
_MODEL_PY = (pathlib.Path(__file__).resolve().parent.parent
             / 'models' / 'ir_ui_menu.py')


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
        for key in ('id', 'menu_id', 'xmlid', 'name', 'web_icon',
                    'web_icon_data', 'sequence'):
            self.assertIn(key, app)
        # #477: no action id is published. The client holds the per-user action
        # for every app root in its own menu data; a second answer computed
        # server-side is what made the launcher and the menu service disagree.
        self.assertNotIn('action_id', app)

    # ------------------------------------------ inert cards (#459)
    def test_an_app_whose_root_menu_has_no_action_still_opens(self):
        """THE #459 DEFECT, PINNED. CRM, Calendar and Contacts all have root
        menus with `action = False` — they are containers, and the action lives
        on a child. The launcher returned that empty action, so those cards did
        nothing when clicked while Dashboard and Discuss (which own actions)
        worked. Every app must resolve to the menu that actually owns an
        action."""
        container = self.Menu.create({'name': 'Container App'})
        # The child that owns the action. Not asserted on directly any more —
        # #477 navigates to the ROOT — but it is what gives Odoo an action to
        # resolve onto the root, so the fixture still needs it.
        self.Menu.create({
            'name': 'Container App / Things', 'parent_id': container.id,
            'action': 'ir.actions.act_window,%s' % self.action.id,
            'sequence': 5,
        })
        self.env['ir.model.data'].create({
            'name': 'menu_root', 'module': 'container_app',
            'model': 'ir.ui.menu', 'res_id': container.id})
        self.Config.create({
            'plan_code': 'TEST', 'allowed_module_names': 'container_app'})
        self.env.registry.clear_cache()

        apps = [a for a in self.Menu.nc_tenant_apps() if a['name'] == 'Container App']
        self.assertEqual(len(apps), 1, "the container app must still be offered")
        app = apps[0]
        self.assertFalse(container.action, "fixture check: the root owns no action")
        # #477: the card navigates to the APP ROOT, not to the resolved child.
        # Odoo's menu service keys on the root and has already attached the
        # resolved action to it; a descendant id need not exist in that map at
        # all, which is what made every Sales click throw.
        self.assertEqual(app['menu_id'], container.id,
                         "the card must navigate to the app root")
        self.assertEqual(app['id'], container.id)

    def test_an_app_with_no_reachable_action_is_not_offered_at_all(self):
        """A card that can open nothing is worse than an absent one: it
        advertises a feature and then does nothing. Such an app is dropped
        rather than rendered dead."""
        empty = self.Menu.create({'name': 'Empty App'})
        self.env['ir.model.data'].create({
            'name': 'menu_root', 'module': 'empty_app',
            'model': 'ir.ui.menu', 'res_id': empty.id})
        self.env.registry.clear_cache()
        self.assertNotIn('Empty App', self._app_names())

    def test_every_app_is_navigable_with_the_clients_own_menu_data(self):
        """THE #477 CRASH, PINNED.

        `menu.selectMenu(id)` does `menusData[id].actionID` with no guard, and
        `menusData` is `load_web_menus()` keyed by menu id. So every id this
        payload offers for navigation MUST be a key in that map with a resolved
        action — otherwise the client reads `.actionID` off undefined and the
        whole app crashes with a TypeError.

        Before the fix the payload offered a resolved DESCENDANT. For Sales
        that descendant was "Quotation Templates" (a Configuration item, chosen
        because the old resolver took a global (sequence, id) minimum over the
        subtree instead of Odoo's first-child descent) and it is not in the
        client's menu data at all.
        """
        web_menus = self.Menu.sudo().load_web_menus(False)
        apps = self.Menu.nc_tenant_apps()
        self.assertTrue(apps, "control: no apps, so this asserts nothing")
        for app in apps:
            self.assertIn(
                app['id'], web_menus,
                "app %r offers menu id %s, which the client's menu service "
                "does not know — selectMenu() would throw" % (app['name'], app['id']))
            self.assertTrue(
                web_menus[app['id']]['actionID'],
                "app %r resolves to a menu with no action" % app['name'])
            self.assertEqual(app['menu_id'], app['id'],
                             "navigation must target the app root")

    def test_the_launcher_renders_for_a_tenant_whose_plan_is_narrow(self):
        """`nc_tenant_apps` must not go through Odoo's `load_web_menus()`.

        That walks `_load_menus_blacklist()`, and `hr`'s hook there does an
        unconditional `hr.department` read for any user outside
        `hr.group_hr_user`. Ring 2 refuses that on a tenant whose plan does not
        license `hr` — which is every tenant that had `hr` REVOKED, since
        revocation deliberately never uninstalls. Reading the authoritative
        source would therefore raise AccessError for exactly the tenants whose
        entitlement is narrowest, and their home page would not render at all.

        Caught by the CI matrix rather than by review: this test class runs
        with `hr` installed and a deliberately narrow plan.
        """
        self.Config.create({'plan_code': 'TEST', 'allowed_module_names': 'lic_app'})
        self.env.registry.clear_cache()
        probe = self.env['res.users'].create({
            'name': 'Narrow Plan Probe', 'login': 'nc_narrow_probe',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])],
        })
        # Must not raise. The assertion is that the call COMPLETES; what it
        # returns is covered by the tests above.
        apps = self.Menu.with_user(probe).nc_tenant_apps()
        self.assertIsInstance(apps, list)

    def test_the_launcher_navigates_through_the_menu_service(self):
        """#459 asked for real Odoo navigation, not hand-built URLs. The
        component must call the menu service's selectMenu with the resolved
        menu id — that is what also sets the current-app highlight."""
        source = _HOME_JS.read_text(encoding='utf-8')
        self.assertIn('useService("menu")', source)
        # #477: the ROOT id. `app.menu_id` was a resolved descendant that the
        # menu service need not know about.
        self.assertIn('selectMenu(app.id)', source)
        self.assertNotIn('window.location', source,
                         "navigate through the menu service, never a URL")

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
