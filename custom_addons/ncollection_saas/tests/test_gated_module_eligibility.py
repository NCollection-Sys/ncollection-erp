# -*- coding: utf-8 -*-
"""Eligibility vs activation of a GATED module (UAE e-invoicing), through the one authority and the one override.

ELIGIBLE is a computed fact about a tenant (country AND books verified AND Accounting entitled); ACTIVATED is an
explicit per-tenant ``enable`` override. These tests pin both halves and the ways they must not blur:

  * UAE tenant A enabled, UAE tenant B not, UAE tenant C enabled, a non-UAE tenant never eligible;
  * the registry's country LABEL is not enough: a tenant registered as AE whose books were refused is not eligible;
  * a plan that names the module does not activate it; the picker never offers it;
  * losing eligibility revokes the licence without touching the override row or any data;
  * enabling twice changes nothing; withdrawing is reversible; there is no bulk path.

The mechanism is exercised on a real, always-present module name (``crm``) by patching the gate table, so these tests
do
not depend on the engine module being installed; the real table entry is asserted separately."""

from unittest.mock import patch

from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged

from odoo.addons.ncollection_subscription.models.gated_modules import GATED_MODULES

GATE = 'crm'
RULE = {'country': 'AE', 'requires': ('account',), 'label': 'Test gate'}


@tagged('post_install', '-at_install')
class TestGatedModuleEligibility(TransactionCase):

    _seq = 0

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Plan = cls.env['ncollection.subscription.plan']
        cls.accounting = Plan.create(
            {'name': 'GateAcct', 'code': 'GATEACCT', 'max_users': 5, 'allowed_module_names': 'account'}
        )
        cls.no_accounting = Plan.create(
            {'name': 'GateNoAcct', 'code': 'GATENOACCT', 'max_users': 5, 'allowed_module_names': 'sale'}
        )
        cls.names_the_gate = Plan.create(
            {'name': 'GateNamed', 'code': 'GATENAMED', 'max_users': 5, 'allowed_module_names': 'account,%s' % GATE}
        )
        cls.ae = cls.env.ref('base.ae')
        cls.us = cls.env.ref('base.us')

    def setUp(self):
        super().setUp()
        patcher = patch.dict(GATED_MODULES, {GATE: RULE})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.pushes = []

        def fake_push(tenants):
            self.pushes.extend(tenants.mapped('database_name'))

        pusher = patch.object(type(self.env['ncollection.tenant']), '_nc_push_entitlement_change', new=fake_push)
        pusher.start()
        self.addCleanup(pusher.stop)

    def _tenant(self, plan=None, country=None, state='done', message='already ae/AED'):
        type(self)._seq += 1
        return self.env['ncollection.tenant'].create(
            {
                'company_name': 'Gate Co',
                'database_name': 'gateco%d' % self._seq,
                'plan_id': (plan or self.accounting).id,
                'country_id': (country or self.ae).id,
                'database_status': 'ready',
                'localization_applied_state': state,
                'localization_applied_message': message,
            }
        )

    # ------------------------------------------------------------------ the verified fact
    def test_books_are_verified_only_by_a_done_check_naming_the_packages_chart_and_currency(self):
        self.assertTrue(self._tenant(message='already ae/AED')._nc_books_verified())
        self.assertTrue(self._tenant(message='ae/AED')._nc_books_verified())

    def test_every_other_recorded_state_is_not_verified(self):
        for state in ('none', 'refused', 'error'):
            self.assertFalse(self._tenant(state=state)._nc_books_verified(), state)
        self.assertFalse(
            self._tenant(message='already generic_coa/USD')._nc_books_verified(),
            "a done check on the WRONG chart/currency is not a UAE fact",
        )
        self.assertFalse(self._tenant(message='')._nc_books_verified())

    def test_a_tenant_with_no_country_package_is_never_verified(self):
        self.assertFalse(self._tenant(country=self.us, message='already us/USD')._nc_books_verified())

    def test_the_default_without_the_verifying_module_is_fail_closed(self):
        from odoo.addons.ncollection_subscription.models.tenant_eligibility import TenantEligibility

        self.assertFalse(TenantEligibility._nc_books_verified(self._tenant()))

    # ------------------------------------------------------------------ eligible vs activated
    def test_eligible_is_not_activated(self):
        tenant = self._tenant()
        self.assertEqual(tenant._nc_module_eligibility(GATE), (True, []))
        self.assertNotIn(GATE, tenant._nc_effective_module_list(), "eligibility must NOT activate the module")

    def test_an_explicit_enable_activates_exactly_that_tenant(self):
        a, b, c = self._tenant(), self._tenant(), self._tenant()
        a._nc_enable_gated_module(GATE, 'TICKET-A')
        c._nc_enable_gated_module(GATE, 'TICKET-C')
        self.assertIn(GATE, a._nc_effective_module_list())
        self.assertNotIn(GATE, b._nc_effective_module_list(), "eligible but not selected stays untouched")
        self.assertIn(GATE, c._nc_effective_module_list())
        self.assertEqual(
            sorted(set(self.pushes)),
            sorted([a.database_name, c.database_name]),
            "only the selected tenants receive an entitlement push",
        )

    def test_a_non_uae_tenant_is_not_eligible_and_cannot_be_enabled(self):
        tenant = self._tenant(country=self.us, message='already us/USD')
        eligible, reasons = tenant._nc_module_eligibility(GATE)
        self.assertFalse(eligible)
        self.assertIn('AE tenants only', ' '.join(reasons))
        with self.assertRaises(ValidationError):
            tenant._nc_enable_gated_module(GATE, 'TICKET')
        self.assertNotIn(GATE, tenant._nc_effective_module_list())

    def test_the_registry_label_alone_is_not_enough(self):
        """The `wasla` case: registered as AE, but its books were REFUSED (US chart, USD)."""
        for state, message in (('refused', 'REFUSED: holds accounting entries'), ('none', ''), ('error', 'boom')):
            tenant = self._tenant(state=state, message=message)
            eligible, reasons = tenant._nc_module_eligibility(GATE)
            self.assertFalse(eligible, state)
            self.assertIn('not been verified', ' '.join(reasons))
            with self.assertRaises(ValidationError, msg=state):
                tenant._nc_enable_gated_module(GATE, 'TICKET')

    def test_a_tenant_without_accounting_is_not_eligible(self):
        tenant = self._tenant(plan=self.no_accounting)
        eligible, reasons = tenant._nc_module_eligibility(GATE)
        self.assertFalse(eligible)
        self.assertIn('account', ' '.join(reasons))
        with self.assertRaises(ValidationError):
            tenant._nc_enable_gated_module(GATE, 'TICKET')

    def test_a_plan_that_names_the_gated_module_does_not_activate_it(self):
        tenant = self._tenant(plan=self.names_the_gate)
        self.assertIn(GATE, tenant.plan_id.get_allowed_module_list())
        self.assertNotIn(
            GATE,
            tenant._nc_effective_module_list(),
            "a plan line is ignored for a gated module: only the explicit override activates it",
        )

    def test_the_plan_picker_never_offers_a_gated_module(self):
        picker = self.env['ncollection.subscription.plan'].get_selectable_modules()
        offered = {m['name'] for m in picker['optional']} | {m['name'] for m in picker['core']}
        self.assertNotIn(GATE, offered)

    # ------------------------------------------------------------------ revocation, idempotency, reversibility
    def test_losing_eligibility_revokes_the_licence_but_keeps_the_override(self):
        tenant = self._tenant()
        override = tenant._nc_enable_gated_module(GATE, 'TICKET')
        self.assertIn(GATE, tenant._nc_effective_module_list())
        tenant.localization_applied_state = 'error'  # the books are no longer verified
        self.assertNotIn(GATE, tenant._nc_effective_module_list())
        self.assertTrue(
            override.exists() and override.active and override.mode == 'enable',
            "revocation is not deletion: the decision stays on record",
        )
        tenant.localization_applied_state = 'done'
        self.assertIn(GATE, tenant._nc_effective_module_list(), "verification restored => entitlement restored")

    def test_enabling_twice_is_a_no_op(self):
        tenant = self._tenant()
        first = tenant._nc_enable_gated_module(GATE, 'TICKET-1')
        before = tenant._nc_effective_module_list()
        second = tenant._nc_enable_gated_module(GATE, 'TICKET-1')
        self.assertEqual(first, second, "same override row, not a duplicate")
        self.assertEqual(
            self.env['ncollection.tenant.module.override']
            .with_context(active_test=False)
            .search_count([('tenant_id', '=', tenant.id), ('module_name', '=', GATE)]),
            1,
        )
        self.assertEqual(tenant._nc_effective_module_list(), before)

    def test_withdrawing_reverses_it_and_nothing_else(self):
        tenant = self._tenant()
        tenant._nc_enable_gated_module(GATE, 'TICKET')
        other = list(tenant._nc_effective_module_list())
        tenant._nc_withdraw_gated_module(GATE)
        self.assertNotIn(GATE, tenant._nc_effective_module_list())
        self.assertEqual([m for m in other if m != GATE], tenant._nc_effective_module_list())
        tenant._nc_enable_gated_module(GATE, 'TICKET-AGAIN')
        self.assertIn(GATE, tenant._nc_effective_module_list(), "reversible both ways")

    def test_a_reason_is_mandatory(self):
        with self.assertRaises(UserError):
            self._tenant()._nc_enable_gated_module(GATE, '   ')

    def test_the_reason_is_enforced_where_the_decision_is_stored_not_only_in_the_helper(self):
        tenant = self._tenant()
        Override = self.env['ncollection.tenant.module.override']
        for reason in (False, '', '   '):
            with self.assertRaises(ValidationError, msg=repr(reason)):
                Override.create({'tenant_id': tenant.id, 'module_name': GATE, 'mode': 'enable', 'reason': reason})
        self.assertNotIn(GATE, tenant._nc_effective_module_list())
        Override.create({'tenant_id': tenant.id, 'module_name': GATE, 'mode': 'enable', 'reason': 'TICKET-1'})
        self.assertIn(GATE, tenant._nc_effective_module_list())

    def test_a_tenant_whose_verified_books_are_later_refused_is_pushed_at_once(self):
        tenant = self._tenant()  # state 'done'
        tenant._nc_enable_gated_module(GATE, 'TICKET-1')
        enqueued = []
        with patch.object(
            type(tenant), '_run_odoo_subprocess', side_effect=Exception('REFUSED: books are not UAE books')
        ), patch.object(type(tenant), '_config_sync_enqueue', lambda self: enqueued.append(self.database_name)):
            tenant._nc_apply_localization_one()
        self.assertEqual(tenant.localization_applied_state, 'refused')
        self.assertEqual(
            enqueued, [tenant.database_name], "the withdrawal reaches the tenant now, not at the reconcile"
        )
        self.assertNotIn(GATE, tenant._nc_effective_module_list())

    def test_a_tenant_that_was_never_verified_pushes_nothing_when_refused(self):
        tenant = self._tenant(state='none', message='')
        enqueued = []
        with patch.object(type(tenant), '_run_odoo_subprocess', side_effect=Exception('REFUSED: x')), patch.object(
            type(tenant), '_config_sync_enqueue', lambda self: enqueued.append(self.database_name)
        ):
            tenant._nc_apply_localization_one()
        self.assertEqual(enqueued, [])

    def test_only_gated_modules_use_the_gated_path(self):
        with self.assertRaises(UserError):
            self._tenant()._nc_enable_gated_module('sale', 'TICKET')

    def test_a_disable_override_for_a_gated_module_needs_no_eligibility(self):
        """Withholding is always allowed; only granting is gated."""
        tenant = self._tenant(country=self.us, message='already us/USD')
        self.env['ncollection.tenant.module.override'].create(
            {'tenant_id': tenant.id, 'module_name': GATE, 'mode': 'disable', 'reason': 'x'}
        )

    # ------------------------------------------------------------------ nothing is changed for non-gated modules
    def test_non_gated_modules_and_other_tenants_are_unaffected(self):
        tenant = self._tenant()
        other = self._tenant(plan=self.no_accounting, country=self.us, message='already us/USD')
        before_other = other._nc_effective_module_list()
        tenant._nc_enable_gated_module(GATE, 'TICKET')
        self.assertEqual(other._nc_effective_module_list(), before_other)
        self.assertIn('account', tenant._nc_effective_module_list())

    def test_there_is_no_bulk_or_country_wide_enable(self):
        tenant_model = self.env['ncollection.tenant']
        for name in dir(type(tenant_model)):
            lowered = name.lower()
            self.assertFalse('enable_all' in lowered or 'bulk' in lowered or 'all_uae' in lowered, name)
        servers = self.env['ir.actions.server'].search([('code', 'ilike', GATE)])
        self.assertFalse(servers, "no server action may enable a gated module across tenants")
        tenant_a, tenant_b = self._tenant(), self._tenant()
        with self.assertRaises(ValueError):
            (tenant_a | tenant_b)._nc_enable_gated_module(GATE, 'TICKET')  # ensure_one: one tenant at a time


@tagged('post_install', '-at_install')
class TestTheRealGateTable(TransactionCase):
    """The real table entry and the real module, end to end through the one override and the one authority."""

    MODULE = 'ncollection_uae_einvoicing'
    _seq = 0

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.plan = cls.env['ncollection.subscription.plan'].create(
            {'name': 'RealGate', 'code': 'REALGATE', 'max_users': 5, 'allowed_module_names': 'account'}
        )
        cls.ae, cls.us = cls.env.ref('base.ae'), cls.env.ref('base.us')

    def setUp(self):
        super().setUp()
        self.pushed = []
        pusher = patch.object(
            type(self.env['ncollection.tenant']),
            '_nc_push_entitlement_change',
            new=lambda tenants: self.pushed.extend(tenants.mapped('database_name')),
        )
        pusher.start()
        self.addCleanup(pusher.stop)

    def _tenant(self, country, state, message):
        type(self)._seq += 1
        return self.env['ncollection.tenant'].create(
            {
                'company_name': 'Real Gate Co',
                'database_name': 'realgate%d' % self._seq,
                'plan_id': self.plan.id,
                'country_id': country.id,
                'database_status': 'ready',
                'localization_applied_state': state,
                'localization_applied_message': message,
            }
        )

    def test_uae_einvoicing_is_gated_to_ae_and_needs_accounting(self):
        rule = GATED_MODULES[self.MODULE]
        self.assertEqual(rule['country'], 'AE')
        self.assertEqual(tuple(rule['requires']), ('account',))

    def test_the_module_exists_on_this_platform_and_is_not_an_app(self):
        module = self.env['ir.module.module'].search([('name', '=', self.MODULE)])
        self.assertTrue(module, 'the engine module must be discoverable')
        self.assertFalse(module.application)

    def test_a_verified_uae_tenant_is_eligible_and_activation_is_a_separate_explicit_step(self):
        tenant = self._tenant(self.ae, 'done', 'already ae/AED')
        self.assertEqual(tenant._nc_module_eligibility(self.MODULE), (True, []))
        self.assertNotIn(self.MODULE, tenant._nc_effective_module_list())
        tenant._nc_enable_gated_module(self.MODULE, 'PILOT-1')
        self.assertIn(self.MODULE, tenant._nc_effective_module_list())
        self.assertEqual(self.pushed, [tenant.database_name])

    def test_the_registry_ae_tenant_whose_books_are_us_is_refused(self):
        tenant = self._tenant(self.ae, 'refused', 'REFUSED: US chart in USD')
        with self.assertRaises(ValidationError):
            tenant._nc_enable_gated_module(self.MODULE, 'PILOT-2')
        self.assertNotIn(self.MODULE, tenant._nc_effective_module_list())

    def test_a_non_uae_tenant_is_not_eligible(self):
        tenant = self._tenant(self.us, 'done', 'already us/USD')
        self.assertFalse(tenant._nc_module_eligibility(self.MODULE)[0])
        with self.assertRaises(ValidationError):
            tenant._nc_enable_gated_module(self.MODULE, 'PILOT-3')

    def test_three_uae_tenants_enable_one_leave_one_untouched_and_a_non_uae_one_is_never_eligible(self):
        a = self._tenant(self.ae, 'done', 'already ae/AED')
        b = self._tenant(self.ae, 'done', 'already ae/AED')
        c = self._tenant(self.ae, 'done', 'already ae/AED')
        egypt = self._tenant(self.env.ref('base.eg'), 'none', '')
        a._nc_enable_gated_module(self.MODULE, 'A')
        c._nc_enable_gated_module(self.MODULE, 'C')
        self.assertEqual(
            [self.MODULE in t._nc_effective_module_list() for t in (a, b, c, egypt)], [True, False, True, False]
        )
        self.assertTrue(b._nc_module_eligibility(self.MODULE)[0], 'B stays eligible but unselected')
        self.assertFalse(egypt._nc_module_eligibility(self.MODULE)[0])

    def test_provisioning_twice_is_idempotent(self):
        tenant = self._tenant(self.ae, 'done', 'already ae/AED')
        tenant._nc_enable_gated_module(self.MODULE, 'PILOT')
        first = (
            tenant._nc_effective_module_list(),
            self.env['ncollection.tenant.module.override']
            .with_context(active_test=False)
            .search_count([('tenant_id', '=', tenant.id)]),
        )
        tenant._nc_enable_gated_module(self.MODULE, 'PILOT')
        second = (
            tenant._nc_effective_module_list(),
            self.env['ncollection.tenant.module.override']
            .with_context(active_test=False)
            .search_count([('tenant_id', '=', tenant.id)]),
        )
        self.assertEqual(first, second)
        self.assertEqual(second[1], 1)

    def test_withdrawing_the_pilot_is_reversible_and_touches_nothing_else(self):
        tenant = self._tenant(self.ae, 'done', 'already ae/AED')
        before = tenant._nc_effective_module_list()
        tenant._nc_enable_gated_module(self.MODULE, 'PILOT')
        tenant._nc_withdraw_gated_module(self.MODULE)
        self.assertEqual(tenant._nc_effective_module_list(), before)
