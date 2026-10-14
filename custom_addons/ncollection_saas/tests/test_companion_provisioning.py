# -*- coding: utf-8 -*-
"""Cheque printing follows Accounting through EVERY reader of the entitlement, not just the display field.

``ncollection.tenant._nc_effective_module_list`` is the one authority (ncollection_subscription/models/companions.py
feeds it). Four things read it independently, and "installed but unlicensed" / "licensed but never installed" is what
happens when one of them disagrees: provisioning's ``_module_list`` (a NEW tenant), the module-install job (an EXISTING
tenant), config sync (the tenant's own licence), and the plan-save trigger. These tests drive each of them, plus the
migration that reaches tenants that exist today.

The subprocess is intercepted, as in test_module_install: spawning a real ``odoo -i`` belongs to the provisioning suite
(``verify_provisioning.sh`` provisions real databases and asserts the companion there)."""
import importlib.util
import os
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

CHEQUE = 'ncollection_cheque_print'
TENANT_INSTALL = 'odoo.addons.ncollection_saas.models.module_install.TenantModuleInstall'
MIGRATION = os.path.join(os.path.dirname(__file__), '..', 'migrations', '19.0.6.16.0', 'post-migrate.py')


@tagged('post_install', '-at_install')
class TestCompanionProvisioning(TransactionCase):

    _seq = 0

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Plan = cls.env['ncollection.subscription.plan']
        cls.with_accounting = Plan.create({'name': 'Acct', 'code': 'CPACCT', 'max_users': 5,
                                           'allowed_module_names': 'crm,account'})
        cls.without_accounting = Plan.create({'name': 'NoAcct', 'code': 'CPNOACCT', 'max_users': 5,
                                              'allowed_module_names': 'crm,sale'})

    def _tenant(self, plan, ready=True):
        type(self)._seq += 1
        return self.env['ncollection.tenant'].create({
            'company_name': 'Companion Co', 'database_name': 'cmpprov%d' % self._seq, 'plan_id': plan.id,
            'database_status': 'ready' if ready else 'not_provisioned', 'email': 'owner@companion.example'})

    def _job(self, tenant):
        return self.env['ncollection.provisioning.job'].create(
            {'tenant_id': tenant.id, 'database_name': tenant.database_name, 'status': 'queued'})

    # ------------------------------------------------------------------ a NEW tenant: the provisioning `-i`
    def test_a_new_tenant_with_accounting_is_provisioned_with_cheque_printing(self):
        modules = self._job(self._tenant(self.with_accounting, ready=False))._module_list()
        self.assertEqual(modules[:4], ['base', 'ncollection_core', 'ncollection_branding', 'ncollection_auth'])
        self.assertIn('account', modules)
        self.assertEqual(modules.count(CHEQUE), 1)

    def test_a_new_tenant_without_accounting_is_not(self):
        modules = self._job(self._tenant(self.without_accounting, ready=False))._module_list()
        self.assertNotIn(CHEQUE, modules)
        self.assertNotIn('account', modules, "the companion must never bring its host in by itself")

    def test_a_plan_that_names_it_without_accounting_still_provisions_neither(self):
        plan = self.env['ncollection.subscription.plan'].create(
            {'name': 'Typed', 'code': 'CPTYPED', 'max_users': 5, 'allowed_module_names': 'crm,%s' % CHEQUE})
        modules = self._job(self._tenant(plan, ready=False))._module_list()
        self.assertNotIn(CHEQUE, modules)
        self.assertNotIn('account', modules)

    # ------------------------------------------------------------------ an EXISTING tenant: the install job
    def _capture_install(self):
        captured = {}

        def fake_run(_self, cmd, label, stdin=None, env=None, timeout=None):
            captured['cmd'] = cmd
            return "Modules loaded."

        patcher = patch('odoo.addons.ncollection_saas.models.saas_subprocess.SaasSubprocessMixin._run_odoo_subprocess',
                        new=fake_run)
        patcher.start()
        self.addCleanup(patcher.stop)
        return captured

    def test_the_install_job_installs_it_into_an_existing_tenant_with_accounting(self):
        tenant = self._tenant(self.with_accounting)
        self.assertIn(CHEQUE, tenant._nc_licensed_module_list())
        captured = self._capture_install()
        self.assertTrue(tenant.run_module_install())
        installed = captured['cmd'][captured['cmd'].index('-i') + 1].split(',')
        self.assertEqual(sorted(installed), sorted(['crm', 'account', CHEQUE]))
        self.assertEqual(captured['cmd'][captured['cmd'].index('-d') + 1], tenant.database_name)

    def test_the_install_job_never_installs_it_into_a_tenant_without_accounting(self):
        tenant = self._tenant(self.without_accounting)
        captured = self._capture_install()
        tenant.run_module_install()
        installed = captured['cmd'][captured['cmd'].index('-i') + 1].split(',')
        self.assertEqual(sorted(installed), ['crm', 'sale'])

    # ------------------------------------------------------------------ the tenant's own licence (config sync)
    def test_the_tenants_licence_names_it_exactly_when_accounting_is_licensed(self):
        with_cheque = self._tenant(self.with_accounting)._config_sync_vals()['allowed_module_names'].split(',')
        self.assertIn('account', with_cheque)
        self.assertIn(CHEQUE, with_cheque)
        without = self._tenant(self.without_accounting)._config_sync_vals()['allowed_module_names'].split(',')
        self.assertNotIn(CHEQUE, without)

    def test_the_licence_pushed_to_a_tenant_never_widens_the_plans_business_modules(self):
        """Companions add native modules only. No non-NCollection module appears that the plan did not name."""
        pushed = set(self._tenant(self.with_accounting)._config_sync_vals()['allowed_module_names'].split(','))
        self.assertEqual({m for m in pushed if not m.startswith('ncollection_')}, {'crm', 'account'})

    # ------------------------------------------------------------------ the plan-save trigger
    def test_giving_a_plan_accounting_queues_installs_that_include_the_companion(self):
        plan = self.env['ncollection.subscription.plan'].create(
            {'name': 'Grow', 'code': 'CPGROW', 'max_users': 5, 'allowed_module_names': 'crm'})
        tenant = self._tenant(plan)
        queued = []
        with patch('%s._nc_enqueue_module_install' % TENANT_INSTALL, new=lambda recs: queued.extend(recs)):
            plan.allowed_module_names = 'crm,account'
        self.assertIn(tenant, queued, "a plan gaining Accounting must queue the install for its ready tenants")
        self.assertIn(CHEQUE, tenant._nc_licensed_module_list())

    def test_taking_accounting_away_queues_no_install_and_never_uninstalls(self):
        tenant = self._tenant(self.with_accounting)
        queued = []
        with patch('%s._nc_enqueue_module_install' % TENANT_INSTALL, new=lambda recs: queued.extend(recs)):
            self.with_accounting.allowed_module_names = 'crm'
        self.assertEqual(queued, [])
        self.assertNotIn(CHEQUE, tenant._nc_licensed_module_list())    # licence withdrawn with the host
        self.assertTrue(hasattr(tenant, 'run_module_install'))

    # ------------------------------------------------------------------ the migration: tenants that exist today
    def _migrate(self):
        spec = importlib.util.spec_from_file_location('companion_migration_19_0_6_16_0', MIGRATION)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.migrate

    def test_the_migration_queues_only_the_ready_tenants_that_gain_a_companion(self):
        gaining = self._tenant(self.with_accounting)
        unaffected = self._tenant(self.without_accounting)
        not_ready = self._tenant(self.with_accounting, ready=False)
        synced, installed = [], []
        with patch('%s._nc_enqueue_module_install' % TENANT_INSTALL, new=lambda recs: installed.extend(recs)), \
                patch('odoo.addons.ncollection_saas.models.config_sync.TenantConfigSync._config_sync_enqueue',
                      new=lambda recs: synced.extend(recs)):
            self._migrate()(self.env.cr, '19.0.6.15.0')
        for queue in (synced, installed):
            self.assertIn(gaining, queue)
            self.assertNotIn(unaffected, queue, "a tenant without Accounting has nothing new to install")
            self.assertNotIn(not_ready, queue, "only ready tenants have a database to install into")

    def test_the_migration_does_nothing_on_a_fresh_install(self):
        self._tenant(self.with_accounting)
        queued = []
        with patch('%s._nc_enqueue_module_install' % TENANT_INSTALL, new=lambda recs: queued.extend(recs)):
            self._migrate()(self.env.cr, None)
        self.assertEqual(queued, [])
