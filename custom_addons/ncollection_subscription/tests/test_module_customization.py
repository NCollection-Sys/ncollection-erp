# -*- coding: utf-8 -*-
"""The tenant Modules tab: the entitlement matrix and the customize wizard.

The invariant under test is the one the whole feature exists for:

    customising tenant A changes tenant A and NOTHING ELSE

— not the plan, not tenant B, not the localization package. The wizard is only
an editor for per-tenant override rows, so every assertion here is ultimately
about those rows and the single authority (`_nc_effective_module_list`).
"""
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestModuleCustomization(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.plan = cls.env['ncollection.subscription.plan'].create({
            'name': 'Shared', 'code': 'SHAREDPLAN', 'max_users': 5,
            'allowed_module_names': 'crm,sale,account'})
        Tenant = cls.env['ncollection.tenant']
        cls.a = Tenant.create({'company_name': 'Tenant A', 'database_name': 'tenanta',
                               'plan_id': cls.plan.id})
        cls.b = Tenant.create({'company_name': 'Tenant B', 'database_name': 'tenantb',
                               'plan_id': cls.plan.id})
        cls.Wizard = cls.env['ncollection.tenant.module.wizard']

    # ---------------------------------------------------------------- helpers
    def _wizard(self, tenant):
        return self.Wizard.with_context(default_tenant_id=tenant.id).create({})

    def _line(self, wizard, name):
        return wizard.line_ids.filtered(lambda ln: ln.module_name == name)

    def _effective(self, tenant):
        return set(tenant._nc_effective_module_list())

    # ----------------------------------------------------------------- matrix
    def test_the_matrix_distinguishes_plan_added_removed_and_result(self):
        self.env['ncollection.tenant.module.override'].create({
            'tenant_id': self.a.id, 'module_name': 'crm', 'mode': 'disable',
            'reason': 'does not sell'})
        self.env['ncollection.tenant.module.override'].create({
            'tenant_id': self.a.id, 'module_name': 'stock', 'mode': 'enable'})
        rows = {r['name']: r for r in self.a._nc_module_rows()}
        self.assertTrue(rows['sale']['in_plan'] and rows['sale']['effective'])
        self.assertEqual(rows['sale']['override'], '')
        self.assertTrue(rows['crm']['in_plan'])
        self.assertEqual(rows['crm']['override'], 'disable')
        self.assertFalse(rows['crm']['effective'])
        self.assertFalse(rows['stock']['in_plan'])
        self.assertEqual(rows['stock']['override'], 'enable')
        self.assertTrue(rows['stock']['effective'])
        html = self.a.module_matrix_html
        self.assertIn('<table', html)
        self.assertIn('does not sell', html, "the reason must be visible")
        for word in ('Included', 'Added', 'Removed', 'Enabled', 'Disabled'):
            self.assertIn(word, html)

    def test_the_matrix_is_html_not_a_comma_separated_string(self):
        self.assertNotIn('crm,sale', self.a.module_matrix_html)

    def test_a_plan_with_no_modules_shows_a_clean_empty_state(self):
        empty_plan = self.env['ncollection.subscription.plan'].create({
            'name': 'Bare', 'code': 'BAREPLAN', 'max_users': 1,
            'allowed_module_names': ''})
        tenant = self.env['ncollection.tenant'].create({
            'company_name': 'Bare Co', 'database_name': 'bareco',
            'plan_id': empty_plan.id})
        self.assertIn('No optional modules yet', tenant.module_matrix_html)
        self.assertNotIn('<table', tenant.module_matrix_html)

    def test_matrix_output_is_escaped(self):
        """A reason is operator-typed text rendered into raw HTML."""
        self.env['ncollection.tenant.module.override'].create({
            'tenant_id': self.a.id, 'module_name': 'crm', 'mode': 'disable',
            'reason': '<script>alert(1)</script>'})
        self.assertNotIn('<script>', self.a.module_matrix_html)
        self.assertIn('&lt;script&gt;', self.a.module_matrix_html)

    # ----------------------------------------------------------------- wizard
    def test_the_wizard_starts_at_the_tenants_current_state(self):
        wiz = self._wizard(self.a)
        self.assertTrue(self._line(wiz, 'crm').enabled)
        self.assertTrue(self._line(wiz, 'crm').in_plan)
        self.assertFalse(self._line(wiz, 'stock').in_plan)
        self.assertFalse(self._line(wiz, 'stock').enabled)

    def test_saving_without_changes_creates_no_overrides(self):
        self._wizard(self.a).action_apply()
        self.assertFalse(self.a.module_override_ids)

    def test_add_and_remove_affect_this_tenant_only(self):
        """THE requirement: plan = A,B,C; tenant adds D, removes B; effective is
        A,C,D here, and the plan and another tenant are untouched."""
        plan_before = self.plan.allowed_module_names
        b_before = self._effective(self.b)

        wiz = self._wizard(self.a)
        self._line(wiz, 'crm').enabled = False      # remove a plan module
        stock = self._line(wiz, 'stock')
        self.assertTrue(stock, "stock must be offered as an addable module")
        stock.enabled = True                         # add a non-plan module
        wiz.reason = 'sells goods, no CRM'
        wiz.action_apply()

        effective = self._effective(self.a)
        self.assertNotIn('crm', effective)
        self.assertIn('stock', effective)
        self.assertTrue({'sale', 'account'} <= effective)
        modes = {o.module_name: o.mode for o in self.a.module_override_ids}
        self.assertEqual(modes, {'crm': 'disable', 'stock': 'enable'})
        self.assertTrue(all(o.reason == 'sells goods, no CRM'
                            for o in self.a.module_override_ids))

        # nothing else moved
        self.assertEqual(self.plan.allowed_module_names, plan_before)
        self.assertEqual(self._effective(self.b), b_before)
        self.assertIn('crm', self._effective(self.b))
        self.assertFalse(self.b.module_override_ids)

    def test_switching_back_to_the_plans_answer_removes_the_override(self):
        wiz = self._wizard(self.a)
        self._line(wiz, 'crm').enabled = False
        wiz.action_apply()
        self.assertEqual(len(self.a.module_override_ids), 1)
        before_plan = self.plan.allowed_module_names

        again = self._wizard(self.a)
        self.assertFalse(self._line(again, 'crm').enabled)
        self._line(again, 'crm').enabled = True
        again.action_apply()
        self.assertFalse(self.a.module_override_ids,
                         "matching the plan needs no override at all")
        self.assertIn('crm', self._effective(self.a))
        self.assertEqual(self.plan.allowed_module_names, before_plan)

    def test_the_change_column_says_what_saving_will_do(self):
        wiz = self._wizard(self.a)
        crm, stock = self._line(wiz, 'crm'), self._line(wiz, 'stock')
        self.assertFalse(crm.change)
        crm.enabled = False
        stock.enabled = True
        self.assertEqual(crm.change, 'removed')
        self.assertEqual(stock.change, 'added')

    def test_a_localization_module_cannot_be_switched_off(self):
        self.a.country_id = self.env.ref('base.ae')
        localization = self.a._nc_localization_modules()
        self.assertTrue(localization, "control: UAE has a localization package")
        wiz = self._wizard(self.a)
        line = self._line(wiz, localization[0])
        self.assertTrue(line.locked)
        line.enabled = False
        wiz.action_apply()
        self.assertIn(localization[0], self._effective(self.a))
        self.assertFalse(self.a.module_override_ids.filtered(
            lambda o: o.module_name == localization[0]))

    def test_only_a_platform_administrator_may_apply(self):
        clerk = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Clerk', 'login': 'modclerk',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        wiz = self._wizard(self.a)
        self._line(wiz, 'crm').enabled = False
        # AccessError is a UserError, so this covers both refusals.
        with self.assertRaises(UserError):
            wiz.with_user(clerk).action_apply()
        self.assertFalse(self.a.module_override_ids)

    def test_the_wizard_posts_the_readonly_identity_of_each_line(self):
        """REGRESSION (found by the click-through, invisible to every test that
        builds lines in Python). The lines are created client-side by
        default_get, and the web client does NOT send a read-only field back on
        save unless it is marked force_save — so module_name arrived NULL and
        saving the wizard died on the override table's NOT NULL constraint."""
        arch = self.env['ncollection.tenant.module.wizard'].get_view(
            view_type='form')['arch']
        from lxml import etree
        tree = etree.fromstring(arch)
        for name in ('module_name', 'label', 'in_plan', 'locked'):
            node = tree.xpath("//field[@name='line_ids']//field[@name='%s']" % name)
            self.assertTrue(node, '%s missing from the wizard list' % name)
            self.assertEqual(node[0].get('force_save'), '1',
                             '%s must be force_save or it is lost on save' % name)
