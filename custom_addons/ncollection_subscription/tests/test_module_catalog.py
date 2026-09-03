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
