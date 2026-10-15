# -*- coding: utf-8 -*-
"""Companion modules: cheque printing FOLLOWS Accounting and has no entitlement of its own.

The rule lives in ONE place, ``ncollection.tenant._nc_effective_module_list`` (provisioning, the module-install job,
config sync and the display field all read it), fed by the table in ``models/companions.py``. These tests pin the
boundary both ways: a tenant with Accounting gets it, a tenant without Accounting does not, and nothing (a plan, an
override, the picker) can turn it into a standalone entitlement, which would license ``account`` through the dependency
closure and widen a plan by accident."""
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged

from ..models.companions import COMPANION_MODULES, companion_host, companions_of

CHEQUE = 'ncollection_cheque_print'


@tagged('post_install', '-at_install')
class TestCompanionModules(TransactionCase):

    _seq = 0

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Plan = cls.env['ncollection.subscription.plan']
        cls.accounting_plan = cls._plan('ACCT', 'crm,account,ncollection_account_reports')
        cls.no_accounting_plan = cls._plan('NOACCT', 'crm,sale')

    @classmethod
    def _plan(cls, code, modules):
        return cls.Plan.create({'name': code, 'code': 'CMP' + code, 'max_users': 5, 'allowed_module_names': modules})

    def _tenant(self, plan, **kw):
        # database_name is UNIQUE (#225): one per call
        type(self)._seq += 1
        vals = {'company_name': 'Companion Co', 'plan_id': plan.id if plan else False,
                'database_name': 'cmpnion%d' % self._seq}
        vals.update(kw)
        return self.env['ncollection.tenant'].create(vals)

    # ------------------------------------------------------------------ the table
    def test_the_table_is_exactly_cheque_printing_following_accounting(self):
        self.assertEqual(COMPANION_MODULES, {CHEQUE: 'account'})
        self.assertEqual(companion_host(CHEQUE), 'account')
        self.assertIsNone(companion_host('account'))
        self.assertEqual(companions_of(['crm', 'account']), [CHEQUE])
        self.assertEqual(companions_of(['crm', 'sale']), [])
        self.assertEqual(companions_of([]), [])

    def test_the_companion_is_a_real_module_that_depends_on_its_host(self):
        """The table must name something installable, and its host must be a real dependency (so the module can never
        be installed in a database without the thing it follows)."""
        module = self.env['ir.module.module'].sudo().search([('name', '=', CHEQUE)])
        self.assertTrue(module, "ncollection_cheque_print is not on this platform's addons path")
        self.assertIn('account', module.dependencies_id.mapped('name'))

    # ------------------------------------------------------------------ the entitlement
    def test_a_tenant_with_accounting_is_entitled_to_cheque_printing(self):
        modules = self._tenant(self.accounting_plan)._nc_effective_module_list()
        self.assertIn('account', modules)
        self.assertEqual(modules.count(CHEQUE), 1)

    def test_a_tenant_without_accounting_is_not(self):
        modules = self._tenant(self.no_accounting_plan)._nc_effective_module_list()
        self.assertNotIn(CHEQUE, modules)
        self.assertEqual(modules, ['crm', 'sale'], "the plan's answer must be exactly what it was")

    def test_a_tenant_with_no_plan_gets_nothing(self):
        self.assertEqual(self._tenant(None)._nc_effective_module_list(), [])

    def test_the_plans_own_answer_is_not_widened(self):
        """Everything the plan named is still there, in order; the companion is the ONLY addition."""
        before = self.accounting_plan.get_allowed_module_list()
        after = self._tenant(self.accounting_plan)._nc_effective_module_list()
        self.assertEqual([m for m in after if m != CHEQUE], before)
        self.assertEqual(set(after) - set(before), {CHEQUE})

    def test_every_plan_on_this_platform_follows_the_same_rule(self):
        """Whatever plans exist (shipped or made later): cheque printing is entitled exactly when Accounting is."""
        plans = self.Plan.with_context(active_test=False).search([])
        self.assertTrue(plans)
        for plan in plans:
            effective = self._tenant(plan)._nc_effective_module_list()
            self.assertEqual(CHEQUE in effective, 'account' in effective,
                             "plan %s: cheque printing must follow Accounting" % plan.code)

    # ------------------------------------------------------------------ no standalone entitlement
    def test_a_plan_cannot_license_it_without_accounting(self):
        """Typing the module into a plan has no effect: it would license 'account' through the dependency closure."""
        plan = self._plan('TYPED', 'crm,%s' % CHEQUE)
        modules = self._tenant(plan)._nc_effective_module_list()
        self.assertEqual(modules, ['crm'])

    def test_a_plan_naming_it_with_accounting_does_not_duplicate_it(self):
        plan = self._plan('BOTH', 'account,%s' % CHEQUE)
        self.assertEqual(self._tenant(plan)._nc_effective_module_list(), ['account', CHEQUE])

    def test_the_plan_picker_never_offers_it(self):
        catalog = self.Plan.get_selectable_modules()
        offered = {m['name'] for m in catalog['optional']} | {m['name'] for m in catalog['core']}
        self.assertNotIn(CHEQUE, offered, "an operator must not be able to tick a standalone entitlement")
        self.assertIn('account', offered, "control: the host is still selectable")

    def test_an_override_cannot_name_it(self):
        tenant = self._tenant(self.accounting_plan)
        for mode in ('enable', 'disable'):
            with self.assertRaises(ValidationError):
                self.env['ncollection.tenant.module.override'].create(
                    {'tenant_id': tenant.id, 'module_name': CHEQUE, 'mode': mode})

    # ------------------------------------------------------------------ it follows the host, both ways
    def test_disabling_accounting_for_one_tenant_takes_the_companion_with_it(self):
        tenant = self._tenant(self.accounting_plan)
        self.env['ncollection.tenant.module.override'].create(
            {'tenant_id': tenant.id, 'module_name': 'account', 'mode': 'disable', 'reason': 'test'})
        modules = tenant._nc_effective_module_list()
        self.assertNotIn('account', modules)
        self.assertNotIn(CHEQUE, modules)

    def test_adding_accounting_for_one_tenant_brings_the_companion(self):
        tenant = self._tenant(self.no_accounting_plan)
        self.env['ncollection.tenant.module.override'].create(
            {'tenant_id': tenant.id, 'module_name': 'account', 'mode': 'enable', 'reason': 'test'})
        modules = tenant._nc_effective_module_list()
        self.assertIn('account', modules)
        self.assertIn(CHEQUE, modules)

    def test_editing_a_plan_moves_the_companion_with_accounting(self):
        plan = self._plan('MOVE', 'crm')
        tenant = self._tenant(plan)
        self.assertNotIn(CHEQUE, tenant._nc_effective_module_list())
        plan.allowed_module_names = 'crm,account'
        self.assertIn(CHEQUE, tenant._nc_effective_module_list())
        plan.allowed_module_names = 'crm'
        self.assertNotIn(CHEQUE, tenant._nc_effective_module_list())

    # ------------------------------------------------------------------ what the operator sees
    def test_the_display_field_mirrors_the_authority(self):
        tenant = self._tenant(self.accounting_plan)
        self.assertEqual([m.strip() for m in tenant.effective_module_names.split(',')],
                         tenant._nc_effective_module_list())
        self.assertIn(CHEQUE, tenant.effective_module_names)

    def test_the_module_matrix_shows_it_as_following_accounting_with_no_switch(self):
        rows = {row['name']: row for row in self._tenant(self.accounting_plan)._nc_module_rows()}
        row = rows[CHEQUE]
        self.assertTrue(row['effective'])
        self.assertTrue(row['locked'], "a companion has no switch of its own")
        self.assertTrue(row['follows'])
        self.assertEqual(rows['account']['follows'], '')
        self.assertNotIn(CHEQUE, {r['name'] for r in self._tenant(self.no_accounting_plan)._nc_module_rows()})

    def test_the_customize_wizard_cannot_switch_it_off(self):
        tenant = self._tenant(self.accounting_plan)
        wizard = self.env['ncollection.tenant.module.wizard'].with_context(default_tenant_id=tenant.id).create(
            {'tenant_id': tenant.id})
        line = wizard.line_ids.filtered(lambda line: line.module_name == CHEQUE)
        self.assertTrue(line and line.locked)
        line.enabled = False
        wizard.sudo().action_apply()
        self.assertFalse(tenant.module_override_ids)
        self.assertIn(CHEQUE, tenant._nc_effective_module_list())
