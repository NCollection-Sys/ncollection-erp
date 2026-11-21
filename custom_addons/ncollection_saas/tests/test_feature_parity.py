# -*- coding: utf-8 -*-
"""Feature parity: a NEW tenant gets the accounting features the existing tenants and the platform have.

THE DEFECT. The enhanced bank / cash journal cards (Current / Pending Balance, Payment · Receipt, Internal Transfer,
Reconcile, To Reconcile, Statements, Batches, Duplicates) live in ``ncollection_journal_operations``, which is
``auto_install`` on top of ``ncollection_bank_reconciliation``. No plan names either, and provisioning installs exactly
``_nc_effective_module_list``; the existing tenants received them by a per-database release ``-i``. So every tenant
provisioned afterwards (Balance Cloud ``fresh``, Atlas Core ``ahmed``) opened on Odoo's standard journal cards, and the
provisioning still reported success, because ``odoo -i`` of the modules it WAS given succeeded.

These tests pin both halves of the fix: the entitlement now names the modules, and provisioning refuses READY when
any module of its ``-i`` did not really install."""
import importlib.util
import os
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

PARITY = ['ncollection_bank_reconciliation', 'ncollection_journal_operations',
          'ncollection_account_invoice_layout', 'ncollection_account_audit']
TENANT_INSTALL = 'odoo.addons.ncollection_saas.models.module_install.TenantModuleInstall'
SYNC = 'odoo.addons.ncollection_saas.models.config_sync.TenantConfigSync._config_sync_enqueue'
MIGRATION = os.path.join(os.path.dirname(__file__), '..', 'migrations', '19.0.6.32.0', 'post-migrate.py')


@tagged('post_install', '-at_install')
class TestFeatureParity(TransactionCase):

    _seq = 0

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Plan = cls.env['ncollection.subscription.plan']
        cls.with_accounting = Plan.create({'name': 'FP Acct', 'code': 'FPACCT', 'max_users': 5,
                                           'allowed_module_names': 'crm,sale,account'})
        cls.without_accounting = Plan.create({'name': 'FP NoAcct', 'code': 'FPNOACCT', 'max_users': 5,
                                              'allowed_module_names': 'crm'})

    def _tenant(self, plan, ready=False):
        type(self)._seq += 1
        return self.env['ncollection.tenant'].create({
            'company_name': 'Parity Co', 'database_name': 'fparity%d' % self._seq, 'plan_id': plan.id,
            'database_status': 'ready' if ready else 'not_provisioned', 'email': 'owner@parity.example'})

    def _job(self, tenant):
        return self.env['ncollection.provisioning.job'].create(
            {'tenant_id': tenant.id, 'database_name': tenant.database_name, 'status': 'queued'})

    # ------------------------------------------------------------------ the entitlement (RED before the fix)
    def test_a_new_accounting_tenant_is_provisioned_with_the_journal_operations_centre(self):
        modules = self._job(self._tenant(self.with_accounting))._module_list()
        for name in PARITY:
            self.assertEqual(modules.count(name), 1, "%s missing from the provisioning -i" % name)

    def test_a_tenant_without_accounting_gets_none_of_them(self):
        modules = self._job(self._tenant(self.without_accounting))._module_list()
        self.assertFalse(set(PARITY) & set(modules))
        self.assertNotIn('account', modules)

    def test_the_sale_matching_module_is_left_to_auto_install(self):
        """It is not an entitlement of its own: naming it with host `sale` would license the bank engine."""
        modules = self._tenant(self.with_accounting)._nc_effective_module_list()
        self.assertNotIn('ncollection_bank_reconciliation_sale', modules)

    def test_every_platform_plan_with_accounting_gets_the_whole_set(self):
        for plan in self.env['ncollection.subscription.plan'].with_context(active_test=False).search([]):
            effective = self._tenant(plan)._nc_effective_module_list()
            for name in PARITY:
                self.assertEqual(name in effective, 'account' in effective, "plan %s / %s" % (plan.code, name))

    # ------------------------------------------------------------------ the gate: no READY on a skipped module
    def test_the_module_gate_sends_the_exact_install_list(self):
        job = self._job(self._tenant(self.with_accounting))
        with patch.object(type(job), '_run_odoo_subprocess', return_value='MODULES_OK=12 modules, 40 views') as run:
            job._verify_modules(job.database_name, ['base', 'account', 'ncollection_journal_operations'])
        env = run.call_args.kwargs['env']
        self.assertEqual(env['NC_EXPECTED_MODULES'], 'base,account,ncollection_journal_operations')
        self.assertNotIn('NC_CONFIG_SYNC_MASTER_KEY', env)
        self.assertIn('--no-http', run.call_args.args[0])
        self.assertIn('12 modules', job.log or '')

    def test_a_failed_module_check_fails_the_job(self):
        job = self._job(self._tenant(self.with_accounting))
        with patch.object(type(job), '_run_odoo_subprocess',
                          side_effect=UserError('module check FAILED: journal operations uninstalled')):
            with self.assertRaises(UserError):
                job._verify_modules(job.database_name, ['ncollection_journal_operations'])

    def test_a_silent_module_check_is_not_success(self):
        job = self._job(self._tenant(self.with_accounting))
        with patch.object(type(job), '_run_odoo_subprocess', return_value='Modules loaded.'):
            with self.assertRaises(UserError):
                job._verify_modules(job.database_name, ['base'])

    def test_the_gate_runs_before_the_tenant_can_become_ready(self):
        tenant = self._tenant(self.with_accounting)
        tenant.write({'registration_state': 'approved', 'admin_login': 'parityadmin'})
        job = self._job(tenant)
        exists = [False]
        dropped = []

        def fake_init(_self, db, modules):
            exists[0] = True

        def fake_drop(_self, db):
            dropped.append(db)
            exists[0] = False

        with patch.object(type(job), '_database_exists', new=lambda _s, db: exists[0]), \
                patch.object(type(job), '_drop_database', new=fake_drop), \
                patch.object(type(job), '_run_odoo_init', new=fake_init), \
                patch.object(type(job), '_verify_localization'), \
                patch.object(type(job), '_verify_modules', side_effect=UserError('module check FAILED')), \
                patch.object(type(job), '_seed_tenant') as seed, \
                patch.object(type(job), '_nc_validate_release') as validate:
            ok = job.run_provisioning()
        self.assertFalse(ok)
        seed.assert_not_called()
        validate.assert_not_called()
        self.assertEqual(job.status, 'failed')
        self.assertNotEqual(tenant.database_status, 'ready')
        self.assertEqual(dropped, [tenant.database_name], 'the half-featured database is rolled back')

    # ------------------------------------------------------------------ existing tenants: the migration
    def _migrate(self):
        spec = importlib.util.spec_from_file_location('parity_migration_19_0_6_32_0', MIGRATION)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.migrate

    def test_the_migration_queues_only_ready_accounting_tenants(self):
        gaining = self._tenant(self.with_accounting, ready=True)
        unaffected = self._tenant(self.without_accounting, ready=True)
        not_ready = self._tenant(self.with_accounting)
        suspended = self._tenant(self.with_accounting, ready=True)
        suspended.write({'status': 'suspended'})
        synced, installed = [], []
        with patch('%s._nc_enqueue_module_install' % TENANT_INSTALL, new=lambda recs: installed.extend(recs)), \
                patch(SYNC, new=lambda recs: synced.extend(recs)):
            self._migrate()(self.env.cr, '19.0.6.31.0')
        self.assertIn(gaining, installed)
        self.assertIn(gaining, synced)
        self.assertNotIn(unaffected, installed)
        self.assertNotIn(not_ready, installed)
        self.assertNotIn(suspended, installed, "a suspended workspace is not changed behind its owner's back")

    def test_the_migration_does_nothing_on_a_fresh_install(self):
        self._tenant(self.with_accounting, ready=True)
        installed = []
        with patch('%s._nc_enqueue_module_install' % TENANT_INSTALL, new=lambda recs: installed.extend(recs)):
            self._migrate()(self.env.cr, None)
        self.assertEqual(installed, [])
