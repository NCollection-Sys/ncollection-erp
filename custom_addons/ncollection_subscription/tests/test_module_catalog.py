# -*- coding: utf-8 -*-
"""The plan module picker's catalog is real, and correctly bounded (#457).

The picker replaced a text field an admin typed `crm,stock` into. Its only new
surface is `get_selectable_modules()`, and the things that can go wrong with it
are all "offers the wrong set":

  * offering a PLATFORM module would invite an admin to license
    ncollection_saas into a tenant database — a two-layer violation with a
    friendly button on it (Rule 3);
  * offering the CORE modules as choices would imply they can be removed, when
    provisioning installs them regardless;
  * returning a catalog that is not the real addons path would let the picker
    offer something that cannot be installed.

Each is asserted with its own control, because "returns nothing" would satisfy
every absence check on its own.
"""
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestSelectableModuleCatalog(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Plan = cls.env['ncollection.subscription.plan']
        cls.catalog = cls.Plan.get_selectable_modules()
        cls.optional_names = {m['name'] for m in cls.catalog['optional']}
        cls.core_names = {m['name'] for m in cls.catalog['core']}

    def test_the_catalog_is_not_empty(self):
        """The control every absence assertion below depends on: an empty
        catalog would pass all of them while making the picker useless."""
        self.assertTrue(self.catalog['optional'],
                        "no selectable modules — the picker would render empty")
        self.assertTrue(self.catalog['core'])

    def test_platform_modules_are_never_offered_to_a_tenant(self):
        """Rule 3. ncollection_saas & co. run on the PLATFORM database; a
        picker that offered them would be a two-layer violation with a button."""
        for platform_module in self.Plan.PLATFORM_ONLY_MODULES:
            self.assertNotIn(platform_module, self.optional_names)

    def test_core_modules_are_reported_as_core_and_never_selectable(self):
        """They are installed whatever the plan says, so offering them as
        choices would imply they can be removed."""
        self.assertEqual(self.core_names, set(self.Plan.CORE_TENANT_MODULES))
        self.assertFalse(self.core_names & self.optional_names,
                         "a core module must not also appear as optional")

    def test_every_entry_carries_what_the_picker_renders(self):
        """The widget shows a name, an official icon and a summary; a missing
        key would render a blank card rather than fail loudly."""
        for module in self.catalog['optional'][:20]:
            for key in ('name', 'label', 'summary', 'icon', 'state'):
                self.assertIn(key, module)
            self.assertTrue(module['label'],
                            "a module must have a display name to be pickable")

    def test_the_catalog_comes_from_the_real_addons_path(self):
        """Real ir.module.module rows, not a hand-maintained list — so the
        picker cannot offer a module that does not exist on this platform."""
        Module = self.env['ir.module.module'].sudo()
        sample = list(self.optional_names)[:15]
        found = set(Module.search([('name', 'in', sample)]).mapped('name'))
        self.assertEqual(found, set(sample))

    def test_uninstallable_modules_are_not_offered(self):
        uninstallable = set(self.env['ir.module.module'].sudo().search(
            [('state', '=', 'uninstallable')]).mapped('name'))
        self.assertFalse(uninstallable & self.optional_names)

    # ---------------------------------------------------- the contract holds
    def test_the_pickers_output_format_is_what_the_plan_already_parses(self):
        """The widget writes a comma-separated technical-name string. The whole
        point of #457 was to change the INPUT METHOD and nothing else, so what
        it produces must round-trip through the plan's existing parser."""
        plan = self.Plan.create({
            'name': 'Picker Plan', 'code': 'PICKPLAN', 'max_users': 5})
        picked = sorted(list(self.optional_names)[:3])
        plan.allowed_module_names = ','.join(picked)
        self.assertEqual(plan.get_allowed_module_list(), picked)


@tagged('post_install', '-at_install')
class TestNativeFinancialModulesAreSelectable(TransactionCase):
    """#467: the native accounting modules can actually be licensed.

    Until this ticket not one ``ncollection_account_*`` module declared
    ``application``, and ``get_selectable_modules()`` filters on exactly that
    — so the picker offered only Odoo and OCA apps, the ENTERPRISE plan could
    therefore name only those, and every native accounting module sat
    ``uninstalled`` in every tenant database while its code, menus and 348
    tests shipped and passed.

    The failure mode was silent in both directions: nothing errored, and no
    test asked the question. These do.
    """

    # The user-facing financial apps. ncollection_account_core is deliberately
    # absent — it ships no menu and no action, so it is dependency-only.
    NATIVE_FINANCIAL_APPS = (
        'ncollection_account_reports',
        'ncollection_account_dashboard',
        'ncollection_account_analytics',
        'ncollection_account_budget',
        'ncollection_account_assets',
        # ncollection_account_localization_uae is deliberately NOT here. #469
        # made it country-driven: provisioning installs it from the tenant's
        # localization package and it is never plan-selectable, because a
        # chart of accounts is not a feature an operator buys and cannot be
        # un-loaded by re-ticking a box. Its exclusion is asserted in
        # test_localization.py.
    )

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Plan = cls.env['ncollection.subscription.plan']
        cls.Module = cls.env['ir.module.module'].sudo()
        cls.catalog = cls.Plan.get_selectable_modules()
        cls.optional_names = {m['name'] for m in cls.catalog['optional']}

    def _installable(self, name):
        """Only assert about modules this addons path can actually install.

        ncollection_account_assets needs OCA account_asset_management, which
        exists only once ./oca has been aggregated (`make oca`), and ./oca is
        generated and gitignored. Skipping it there keeps this test honest
        rather than red for an environment reason — and the modules that ARE
        installable still carry the assertion, so it cannot pass vacuously
        (see the control below).
        """
        module = self.Module.search([('name', '=', name)], limit=1)
        return bool(module) and self.Plan._nc_module_is_installable(module)

    def test_the_native_financial_apps_are_offered_in_the_picker(self):
        checked = 0
        for name in self.NATIVE_FINANCIAL_APPS:
            if not self._installable(name):
                continue
            checked += 1
            self.assertIn(
                name, self.optional_names,
                "%s is not selectable — a plan cannot license it, so it can "
                "never be installed into a tenant (#467)" % name)
        self.assertGreaterEqual(
            checked, 4,
            "control: the financial modules are missing from this addons path, "
            "so the assertion above proved nothing")

    def test_the_dependency_only_base_is_not_offered_as_a_choice(self):
        """ncollection_account_core has no menu and no action. Offering it
        would present a checkbox that changes nothing an operator can see,
        while every sibling already pulls it in as a dependency."""
        self.assertNotIn('ncollection_account_core', self.optional_names)

    def test_each_entry_carries_the_grouping_and_dependency_keys(self):
        """The picker groups by category and shows why a card counts as
        included. A missing key renders a blank filter rather than failing."""
        for module in self.catalog['optional'][:20]:
            self.assertIn('category', module)
            self.assertIn('depends', module)
            self.assertIsInstance(module['depends'], list)

    def test_a_reported_dependency_is_a_real_module_name(self):
        """The picker expands `depends` client-side for display. A name that
        matches no module would render a permanently-unexplained card."""
        # Any offered module that declares a dependency will do — naming one
        # would make this test SKIP wherever that module is absent, and a
        # skipped test counts as a passing one.
        with_deps = [m for m in self.catalog['optional'] if m['depends']]
        self.assertTrue(with_deps,
                        "control: no offered module declares a dependency, so "
                        "the assertion below would prove nothing")
        known = set(self.Module.search([]).mapped('name'))
        for module in with_deps[:10]:
            for dep in module['depends']:
                self.assertIn(
                    dep, known,
                    "%s reports a dependency on %r, which is not a module on "
                    "this platform" % (module['name'], dep))

    def test_a_module_whose_dependency_is_missing_is_never_offered(self):
        """A module that cannot be installed must not be licensable.

        `state != 'uninstallable'` does not catch this: a module whose manifest
        names a dependency that is not on the addons path stays plainly
        `uninstalled`, and Odoo records the gap on the DEPENDENCY row as
        `state == 'unknown'`. Offering it would let an operator queue an
        install job that can only fail — on every ready tenant of the plan, in
        the background, long after the click.

        The live subject is ncollection_account_assets on a tree where ./oca
        has not been aggregated — but CI aggregates it, so searching for a
        real unresolvable module would make this test SKIP exactly where it
        matters most, and a skipped test counts as a passing one. The subject
        is therefore synthesised: an application whose one dependency names no
        module at all, created inside the test transaction and rolled back
        with it.
        """
        ghost = self.Module.create({
            'name': 'nc_test_ghost_dependency_module',
            'shortdesc': 'Ghost Dependency Module',
            'state': 'uninstalled',
            'application': True,
            'dependencies_id': [(0, 0, {'name': 'nc_test_module_that_does_not_exist'})],
        })
        # The control: Odoo leaves such a module plainly `uninstalled`, so the
        # state filter alone would have offered it.
        self.assertEqual(ghost.state, 'uninstalled')
        self.assertFalse(self.Plan._nc_module_is_installable(ghost))
        self.assertNotIn(
            ghost.name,
            {m['name'] for m in self.Plan.get_selectable_modules()['optional']},
            "a module with an unresolvable dependency must not be licensable — "
            "its install job could only fail, on every ready tenant of the plan")
