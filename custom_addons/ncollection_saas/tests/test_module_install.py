# -*- coding: utf-8 -*-
"""Licensed modules actually get installed into an existing tenant (#459).

THE DEFECT. Provisioning installs a plan's modules when the database is
created. After that, a plan change only pushed LICENSING (config sync ->
workspace.config -> Ring 1/Ring 2), so a tenant could be shown an application
that did not exist in their database — which is what happened to Wasla and CRM.

The subprocess is intercepted throughout: spawning a real `odoo -i` against a
real database belongs to the provisioning suite, which already does it. What
these tests own is the DECISION — which modules, for which tenants, driven by
what, and what the tenant claims afterwards.
"""
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

TENANT = 'odoo.addons.ncollection_saas.models.module_install.TenantModuleInstall'


@tagged('post_install', '-at_install')
class TestTenantModuleInstall(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.plan = cls.env['ncollection.subscription.plan'].create({
            'name': 'Install Plan', 'code': 'INSTPLAN',
            'allowed_module_names': 'crm,calendar', 'max_users': 5,
        })
        cls.tenant = cls.env['ncollection.tenant'].create({
            'company_name': 'Install Co', 'database_name': 'instco',
            'plan_id': cls.plan.id, 'database_status': 'ready',
        })

    def _capture_subprocess(self, fail=False):
        """Intercept the isolated odoo subprocess; return the captured argv."""
        captured = {}

        def fake_run(_self, cmd, label, stdin=None, env=None, timeout=None):
            captured['cmd'] = cmd
            captured['label'] = label
            if fail:
                raise UserError(_self.env._("odoo exited 1: boom"))
            return "Modules loaded."

        patcher = patch(
            'odoo.addons.ncollection_saas.models.saas_subprocess'
            '.SaasSubprocessMixin._run_odoo_subprocess', new=fake_run)
        patcher.start()
        self.addCleanup(patcher.stop)
        return captured

    # ------------------------------------------------- what gets installed
    def test_the_plans_modules_are_installed_into_the_tenant_database(self):
        captured = self._capture_subprocess()
        self.assertTrue(self.tenant.run_module_install())

        cmd = captured['cmd']
        self.assertEqual(cmd[0], 'odoo')
        self.assertIn('-i', cmd)
        installed = cmd[cmd.index('-i') + 1].split(',')
        self.assertEqual(sorted(installed), ['calendar', 'crm'])
        self.assertIn('-d', cmd)
        self.assertEqual(cmd[cmd.index('-d') + 1], 'instco',
                         "must install into THIS tenant's database")
        self.assertIn('--stop-after-init', cmd)

    def test_core_modules_are_not_reinstalled(self):
        """They are in every tenant already; listing them again is noise that
        makes the log lie about what changed."""
        self.plan.allowed_module_names = 'crm,base,ncollection_core'
        self.assertEqual(sorted(self.tenant._nc_licensed_module_list()), ['crm'])

    def test_the_module_set_comes_from_the_plan_not_from_a_caller(self):
        """THE SECURITY PROPERTY. `run_module_install` takes no module
        argument at all, so a compromised or mistaken caller cannot name one —
        the worst it achieves is installing what the tenant already pays for."""
        import inspect
        params = set(inspect.signature(
            self.env['ncollection.tenant'].run_module_install).parameters)
        self.assertFalse(
            params - {'self'},
            "run_module_install must take no caller-supplied arguments")

    # -------------------------------------------------------- state honesty
    def test_success_records_installed_and_syncs_config(self):
        captured_sync = []
        patcher = patch(
            'odoo.addons.ncollection_saas.models.config_sync.TenantConfigSync'
            '._config_sync_enqueue',
            new=lambda recs: captured_sync.extend(recs.mapped('database_name')))
        patcher.start()
        self.addCleanup(patcher.stop)
        self._capture_subprocess()

        self.tenant.run_module_install()

        self.assertEqual(self.tenant.module_install_state, 'done')
        self.assertTrue(self.tenant.module_install_last_ok)
        self.assertFalse(self.tenant.module_install_last_error)
        self.assertEqual(captured_sync, ['instco'],
                         "licensing must be re-pushed so it agrees with what "
                         "is now installed")

    def test_failure_is_recorded_and_never_claims_success(self):
        """THE FAIL-SAFE REQUIREMENT. A failed install must not leave the
        tenant asserting the module is ready — that is the state that let a
        licensed-but-missing module look fine."""
        self._capture_subprocess(fail=True)
        self.assertFalse(self.tenant.run_module_install())
        self.assertEqual(self.tenant.module_install_state, 'failed')
        self.assertTrue(self.tenant.module_install_last_error)
        self.assertFalse(self.tenant.module_install_last_ok)

    def test_a_failure_does_not_raise_into_the_callers_transaction(self):
        """Raising would roll back the recorded failure with it, leaving the
        tenant stuck at 'running' — the same reasoning provisioning documents."""
        self._capture_subprocess(fail=True)
        self.tenant.run_module_install()  # must not raise
        self.assertEqual(self.tenant.module_install_state, 'failed')

    # ------------------------------------------------------------- guards
    def test_an_unprovisioned_tenant_is_refused(self):
        unprovisioned = self.env['ncollection.tenant'].create({
            'company_name': 'No DB Co', 'database_name': 'nodbco',
            'plan_id': self.plan.id})
        with self.assertRaises(UserError):
            unprovisioned.action_install_licensed_modules()

    def test_a_plan_with_no_extra_modules_is_refused_rather_than_queued(self):
        """Queueing a subprocess that would install nothing wastes a runner
        slot and produces a log that says nothing happened."""
        self.plan.allowed_module_names = ''
        with self.assertRaises(UserError):
            self.tenant.action_install_licensed_modules()

    def test_a_tenant_with_no_plan_installs_nothing(self):
        self.tenant.plan_id = False
        self.assertEqual(self.tenant._nc_licensed_module_list(), [])
        captured = self._capture_subprocess()
        self.tenant.run_module_install()
        self.assertNotIn('cmd', captured, "no subprocess for an empty set")
        self.assertEqual(self.tenant.module_install_state, 'none')

    # ------------------------------- the plan-save trigger (#461)
    def _capture_enqueued_installs(self):
        """Record which tenants a plan save queues an install for."""
        queued = []

        def fake_enqueue(recs):
            queued.extend(recs.mapped('database_name'))

        patcher = patch('%s._nc_enqueue_module_install' % TENANT, new=fake_enqueue)
        patcher.start()
        self.addCleanup(patcher.stop)
        return queued

    def test_adding_a_module_to_a_plan_queues_installs_automatically(self):
        """THE #461 DEFECT. The plan write hook enqueued LICENSING only, so a
        module added to a plan became visible in the launcher while never
        existing in the tenant's database. Adding one must now start the
        install lifecycle without a second, undiscoverable click."""
        queued = self._capture_enqueued_installs()
        self.plan.allowed_module_names = 'crm,calendar,sale_management'
        self.assertIn('instco', queued,
                      "a plan gaining a module must queue installs for its "
                      "ready tenants")

    def test_resaving_without_a_module_change_queues_nothing(self):
        """Idempotence at the TRIGGER, not just in `odoo -i`. Re-saving a plan
        (or editing only its price) must not spawn installs across every
        tenant on it."""
        queued = self._capture_enqueued_installs()
        self.plan.allowed_module_names = 'crm,calendar'   # same set, rewritten
        self.plan.monthly_price = 999
        self.plan.max_users = 9
        self.assertEqual(queued, [],
                         "no module was added — nothing should be queued")

    def test_reordering_or_respacing_the_same_modules_queues_nothing(self):
        """The comparison is on the PARSED set, so cosmetic edits to the
        stored string are not mistaken for new modules."""
        queued = self._capture_enqueued_installs()
        self.plan.allowed_module_names = ' calendar , crm '
        self.assertEqual(queued, [])

    def test_removing_a_module_queues_no_install_and_no_uninstall(self):
        """Removal is licensing-only: Ring 1 hides it, Ring 2 blocks it, and
        the database keeps its data."""
        queued = self._capture_enqueued_installs()
        self.plan.allowed_module_names = 'crm'   # calendar removed
        self.assertEqual(queued, [],
                         "a removal must not start an install lifecycle")

    def test_adding_several_modules_at_once_is_one_queued_install_per_tenant(self):
        """The engine installs the tenant's whole licensed set, so several new
        modules need one job — not one per module, which would race on the
        same database."""
        queued = self._capture_enqueued_installs()
        self.plan.allowed_module_names = 'crm,calendar,sale_management,contacts'
        self.assertEqual(queued.count('instco'), 1)

    def test_each_tenant_on_the_plan_gets_its_own_job(self):
        """THE INDEPENDENCE REQUIREMENT. Per-tenant jobs are what stop one
        tenant's failure deciding the outcome for the others — a single
        plan-wide job would have one state for many databases."""
        second = self.env['ncollection.tenant'].create({
            'company_name': 'Second Co', 'database_name': 'secondco',
            'plan_id': self.plan.id, 'database_status': 'ready'})
        queued = self._capture_enqueued_installs()
        self.plan.allowed_module_names = 'crm,calendar,sale_management'
        self.assertEqual(sorted(queued), ['instco', 'secondco'])
        self.assertTrue(second.exists())

    def test_only_ready_tenants_are_queued(self):
        """A tenant with no database yet gets its modules from provisioning;
        queueing an install at nothing would fail for a reason that is not a
        fault.

        Exercises the REAL enqueue rather than the patched one — the readiness
        filter lives inside `_nc_enqueue_module_install`, so patching it out
        (as the trigger tests above do) would assert nothing about it. First
        version of this test did exactly that and passed a tenant it should
        have skipped."""
        pending = self.env['ncollection.tenant'].create({
            'company_name': 'Pending Co', 'database_name': 'pendingco',
            'plan_id': self.plan.id, 'database_status': 'not_provisioned'})

        (self.tenant + pending)._nc_enqueue_module_install()

        self.assertEqual(pending.module_install_state, 'none',
                         "an unprovisioned tenant must not be queued")
        self.assertEqual(self.tenant.module_install_state, 'queued')
        self.assertFalse(self.env['queue.job'].search_count([
            ('identity_key', '=', 'nc-module-install-%s' % pending.id)]))
        self.assertTrue(self.env['queue.job'].search_count([
            ('identity_key', '=', 'nc-module-install-%s' % self.tenant.id)]))

    def test_the_real_enqueue_marks_the_tenant_queued_and_is_deduplicated(self):
        """Exercises the real `_nc_enqueue_module_install` (not the patched
        one): the tenant must show 'queued' so the admin sees work pending,
        and a second save must not stack a duplicate job."""
        self.tenant._nc_enqueue_module_install()
        self.assertEqual(self.tenant.module_install_state, 'queued')
        jobs = self.env['queue.job'].search([
            ('identity_key', '=', 'nc-module-install-%s' % self.tenant.id)])
        self.assertEqual(len(jobs), 1)
        self.tenant._nc_enqueue_module_install()
        self.assertEqual(len(self.env['queue.job'].search([
            ('identity_key', '=', 'nc-module-install-%s' % self.tenant.id)])), 1,
            "identity_key must de-duplicate a pending install")

    def test_a_failing_tenant_does_not_change_another_tenants_state(self):
        """#461's per-tenant independence, asserted on the STATE rather than
        on the queueing: one database failing must leave the other's result
        untouched and its own failure visible."""
        healthy = self.env['ncollection.tenant'].create({
            'company_name': 'Healthy Co', 'database_name': 'healthyco',
            'plan_id': self.plan.id, 'database_status': 'ready'})

        def selective(_self, cmd, label, stdin=None, env=None, timeout=None):
            if 'instco' in cmd:
                raise UserError(_self.env._("odoo exited 1: boom"))
            return "Modules loaded."

        patcher = patch(
            'odoo.addons.ncollection_saas.models.saas_subprocess'
            '.SaasSubprocessMixin._run_odoo_subprocess', new=selective)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.tenant.run_module_install()
        healthy.run_module_install()

        self.assertEqual(self.tenant.module_install_state, 'failed')
        self.assertTrue(self.tenant.module_install_last_error)
        self.assertEqual(healthy.module_install_state, 'done',
                         "one tenant's failure must not affect another's")
        self.assertFalse(healthy.module_install_last_error)

    # --------------------------------------------- revocation is not removal
    def test_revoking_a_module_never_uninstalls_it(self):
        """DELIBERATE PRODUCT BEHAVIOUR, pinned so nobody 'completes' the
        feature by adding an uninstall. Dropping a module's tables destroys
        customer data with no supported way back; a downgrade must cost access,
        not data. Revocation is Ring 1 + Ring 2 only."""
        captured = self._capture_subprocess()
        self.plan.allowed_module_names = 'crm'   # calendar revoked
        self.tenant.run_module_install()
        cmd = captured['cmd']
        self.assertNotIn('-u', cmd)
        for forbidden in ('--uninstall', 'button_uninstall', 'uninstall'):
            self.assertFalse(
                any(forbidden in str(part) for part in cmd),
                "revoking a licence must never trigger an uninstall")
