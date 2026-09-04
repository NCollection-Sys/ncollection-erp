# -*- coding: utf-8 -*-
"""Country-driven localization: the provisioning half (#469).

The behaviour under test is what the ENGINE does with a tenant's country —
which modules it installs, that it refuses to trust an install as evidence, and
that an existing database is never localized automatically. The subprocess
itself is mocked: it shells out to a real `odoo` against a real database, which
belongs in the live verification, not in a unit test.
"""
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestLocalizationProvisioning(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['ir.config_parameter'].sudo().set_param(
            'ncollection_saas.provisioning_quota_per_hour', '0')
        cls.plan = cls.env['ncollection.subscription.plan'].create({
            'name': 'Loc', 'code': 'LOCPROV', 'max_users': 5,
            'allowed_module_names': 'crm'})
        cls.ae = cls.env.ref('base.ae')

    def _tenant(self, **kw):
        vals = {'company_name': 'Loc Co', 'plan_id': self.plan.id,
                'database_name': 'locprov', 'status': 'trial'}
        vals.update(kw)
        return self.env['ncollection.tenant'].create(vals)

    def _job(self, tenant):
        return self.env['ncollection.provisioning.job'].create({
            'tenant_id': tenant.id, 'database_name': tenant.database_name})

    # ---- what gets installed at creation --------------------------------

    def test_a_uae_tenants_first_install_includes_the_package(self):
        """It has to be THIS list, not a later module install: Odoo's `account`
        install schedules a deferred try_loading('generic_coa'), so a tenant
        that gets l10n_ae afterwards has already taken the USD placeholder and
        the real chart would have to load over existing accounting data."""
        job = self._job(self._tenant(country_id=self.ae.id))
        modules = job._module_list()
        for name in ('base_vat', 'l10n_ae', 'ncollection_account_localization_uae'):
            self.assertIn(name, modules)
        self.assertIn('crm', modules)
        self.assertIn('ncollection_core', modules, "core is still unconditional")

    def test_a_tenant_with_no_country_installs_exactly_what_it_did_before(self):
        job = self._job(self._tenant())
        modules = job._module_list()
        for name in ('l10n_ae', 'base_vat', 'ncollection_account_localization_uae'):
            self.assertNotIn(name, modules)

    def test_the_module_list_has_no_duplicates(self):
        """A plan naming the UAE module from before #469 must not make the
        engine pass it to `-i` twice."""
        self.plan.allowed_module_names = 'crm,l10n_ae'
        modules = self._job(self._tenant(country_id=self.ae.id))._module_list()
        self.assertEqual(len(modules), len(set(modules)))

    # ---- licensing follows, through the ONE authority -------------------

    def test_config_sync_licenses_the_localization_package(self):
        """Localization modules are not plan-selectable, so without the union
        Ring 1 would hide the very menus the tenant was provisioned with."""
        tenant = self._tenant(country_id=self.ae.id, database_status='ready')
        licensed = tenant._config_sync_vals()['allowed_module_names']
        self.assertIn('ncollection_account_localization_uae', licensed)
        self.assertIn('crm', licensed)

    def test_the_install_job_and_config_sync_cannot_disagree(self):
        """They read the same authority; a divergence here is what produces a
        module that is licensed but never installed (#461)."""
        tenant = self._tenant(country_id=self.ae.id, database_status='ready')
        licensed = set(tenant._config_sync_vals()['allowed_module_names'].split(','))
        installed = set(tenant._nc_licensed_module_list())
        self.assertEqual(installed, licensed - set(
            self.env['ncollection.subscription.plan'].CORE_TENANT_MODULES))

    # ---- the install is not accepted as evidence ------------------------

    def test_provisioning_verifies_the_result_for_a_localized_tenant(self):
        job = self._job(self._tenant(country_id=self.ae.id))
        with patch.object(type(job), '_run_odoo_subprocess',
                          return_value='LOCALIZATION_OK=ae/AED/12 taxes') as run:
            job._verify_localization(job.database_name)
        self.assertTrue(run.called, "a localized tenant must be checked")
        passed_env = run.call_args.kwargs['env']
        self.assertEqual(passed_env['NC_LOC_CHART'], 'ae')
        self.assertEqual(passed_env['NC_LOC_CURRENCY'], 'AED')
        self.assertIn('ae/AED', job.log or '')

    def test_a_failed_localization_check_fails_the_whole_job(self):
        """The localization hook is fail-soft by design, so a silent skip looks
        exactly like success. If provisioning did not fail here, it would hand
        the customer books that are wrong for their country."""
        job = self._job(self._tenant(country_id=self.ae.id))
        with patch.object(type(job), '_run_odoo_subprocess',
                          side_effect=UserError('localization check FAILED')):
            with self.assertRaises(UserError):
                job._verify_localization(job.database_name)

    def test_a_tenant_with_no_package_is_not_checked(self):
        job = self._job(self._tenant())
        with patch.object(type(job), '_run_odoo_subprocess') as run:
            job._verify_localization(job.database_name)
        self.assertFalse(run.called)


@tagged('post_install', '-at_install')
class TestExistingTenantLocalization(TransactionCase):
    """An existing database is localized by a DECISION, never by a schedule."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.plan = cls.env['ncollection.subscription.plan'].create({
            'name': 'Loc2', 'code': 'LOCEXIST', 'max_users': 5})
        cls.ae = cls.env.ref('base.ae')

    def _tenant(self, **kw):
        vals = {'company_name': 'Existing', 'plan_id': self.plan.id,
                'database_name': 'locexist', 'status': 'active',
                'database_status': 'ready', 'country_id': self.ae.id}
        vals.update(kw)
        return self.env['ncollection.tenant'].create(vals)

    def test_a_tenant_without_a_package_cannot_be_localized(self):
        tenant = self._tenant(country_id=False)
        with self.assertRaises(UserError):
            tenant.action_apply_localization()

    def test_a_tenant_without_a_ready_database_cannot_be_localized(self):
        tenant = self._tenant(database_status='not_provisioned',
                              database_name=False)
        with self.assertRaises(UserError):
            tenant.action_apply_localization()

    def test_a_refusal_is_recorded_as_a_decision_not_a_fault(self):
        """The guard doing its job must not read as a broken button — an
        operator has to be able to tell 'this tenant has books' from 'this
        failed'."""
        tenant = self._tenant()
        with patch.object(type(tenant), '_run_odoo_subprocess',
                          side_effect=UserError(
                              'REFUSED: this tenant has 12 accounting entries.')):
            result = tenant.action_apply_localization()
        # RECORDED, not raised: raising would roll the write back and leave the
        # operator with a dialog and a record that says nothing happened.
        self.assertEqual(result['params']['type'], 'warning')
        self.assertEqual(tenant.localization_applied_state, 'refused')
        self.assertIn('REFUSED', tenant.localization_applied_message)

    def test_a_real_failure_is_recorded_as_an_error(self):
        tenant = self._tenant()
        with patch.object(type(tenant), '_run_odoo_subprocess',
                          side_effect=UserError('connection reset')):
            tenant.action_apply_localization()
        self.assertEqual(tenant.localization_applied_state, 'error')

    def test_a_success_records_the_result_and_re_syncs_licensing(self):
        tenant = self._tenant()
        before = self.env['queue.job'].search_count(
            [('method_name', '=', 'sync_workspace_config')])
        with patch.object(type(tenant), '_run_odoo_subprocess',
                          return_value='LOCALIZATION_APPLIED=ae/AED'):
            tenant.action_apply_localization()
        self.assertEqual(tenant.localization_applied_state, 'done')
        self.assertEqual(tenant.localization_applied_message, 'ae/AED')
        self.assertEqual(
            self.env['queue.job'].search_count(
                [('method_name', '=', 'sync_workspace_config')]),
            before + 1,
            "licensing must follow the localization, through the existing sync")

    def test_the_subprocess_is_told_not_to_force_by_default(self):
        """Force is a human's call. A default that forced would make the guard
        decorative."""
        tenant = self._tenant()
        with patch.object(type(tenant), '_run_odoo_subprocess',
                          return_value='LOCALIZATION_APPLIED=ae/AED') as run:
            tenant.action_apply_localization()
        self.assertEqual(run.call_args.kwargs['env']['NC_LOC_FORCE'], '')

    def test_nothing_in_this_addon_calls_it_automatically(self):
        """The contract that keeps this a button. A cron, a migration or a plan
        write that reached for it would change live tenants' charts of accounts
        unattended — the outcome this whole design exists to prevent."""
        import os
        import re
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        callers = []
        for folder, _dirs, files in os.walk(root):
            if 'tests' in folder or '__pycache__' in folder:
                continue
            for name in files:
                if not name.endswith('.py'):
                    continue
                path = os.path.join(folder, name)
                if os.path.basename(path) == 'tenant_localization.py':
                    continue  # its own definition
                with open(path, encoding='utf-8') as fh:
                    body = fh.read()
                if re.search(r'(action_apply_localization|'
                             r'_nc_apply_localization_one)\s*\(', body):
                    callers.append(path)
        self.assertFalse(
            callers,
            "localization of an existing tenant must stay operator-initiated; "
            "called from: %s" % callers)
