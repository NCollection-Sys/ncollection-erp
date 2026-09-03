# -*- coding: utf-8 -*-
"""Platform-Admin module management actually reaches the tenant (#455).

The gap this pins is not "does a field exist" — it is that the admin-facing
surface must drive the SAME machinery that licenses a tenant, so a change made
in the UI cannot be a no-op. The catalog model (`ncollection.module`) was
exactly that no-op: toggles that reached nothing. These tests assert the real
path and, deliberately, assert that the catalog still reaches nothing, so
nobody "fixes" it into a second source of truth by accident.

The push itself is intercepted: this is the platform database, and
`_config_sync_push` opens an HTTP connection to a tenant. What matters here is
that the right tenants are enqueued with the right payload — the transport is
`test_config_sync.py`'s subject, not this file's.
"""
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

TENANT = 'odoo.addons.ncollection_saas.models.config_sync.TenantConfigSync'


@tagged('post_install', '-at_install')
class TestTenantModuleManagement(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.plan = cls.env['ncollection.subscription.plan'].create({
            'name': 'Mod Plan', 'code': 'MODPLAN',
            'allowed_module_names': 'crm', 'max_users': 5,
        })
        cls.other_plan = cls.env['ncollection.subscription.plan'].create({
            'name': 'Other Plan', 'code': 'OTHERPLAN',
            'allowed_module_names': 'stock', 'max_users': 5,
        })
        cls.tenant = cls.env['ncollection.tenant'].create({
            'company_name': 'Mod Co', 'database_name': 'modco',
            'plan_id': cls.plan.id, 'database_status': 'ready',
        })
        cls.other_tenant = cls.env['ncollection.tenant'].create({
            'company_name': 'Other Co', 'database_name': 'otherco',
            'plan_id': cls.other_plan.id, 'database_status': 'ready',
        })

    def _capture_enqueue(self):
        """Record which tenants would receive a config push."""
        pushed = []

        def fake_enqueue(self_recordset):
            pushed.extend(self_recordset.mapped('database_name'))

        patcher = patch('%s._config_sync_enqueue' % TENANT, new=fake_enqueue)
        patcher.start()
        self.addCleanup(patcher.stop)
        return pushed

    # ------------------------------------------------- the admin's edit path
    def test_editing_a_plans_modules_pushes_to_exactly_that_plans_tenants(self):
        """THE FLOW THE ADMIN UI DRIVES. Editing the plan's module list is what
        the new Modules tab writes, and it must reach that plan's tenants —
        and, just as importantly, must NOT reach anyone else's."""
        pushed = self._capture_enqueue()

        self.plan.allowed_module_names = 'crm,sale'

        self.assertIn('modco', pushed,
                      "a plan's own tenant must receive the new module set")
        self.assertNotIn('otherco', pushed,
                         "a tenant on a DIFFERENT plan must not be touched")

    def test_the_payload_carries_the_new_module_list(self):
        """Enqueuing the right tenant is only half of it — the values actually
        sent must be the edited list, or the tenant is told nothing new."""
        self.plan.allowed_module_names = 'crm,sale,stock'
        vals = self.tenant._config_sync_vals()
        self.assertEqual(vals['allowed_module_names'], 'crm,sale,stock')
        self.assertEqual(vals['plan_code'], 'MODPLAN')

    def test_an_empty_module_list_is_a_valid_plan_not_a_crash(self):
        """A plan with no extra modules is legitimate (core modules only) and
        must serialise as an empty string — the #451 shape, pinned here from
        the admin-edit direction as well."""
        self.plan.allowed_module_names = False
        vals = self.tenant._config_sync_vals()
        self.assertEqual(vals['allowed_module_names'], '')
        self.assertIsInstance(vals['allowed_module_names'], str)

    def test_moving_a_tenant_to_another_plan_relicenses_it(self):
        """Changing plan_id is the other way an admin changes a tenant's
        modules; the effective list must follow the new plan."""
        self.tenant.plan_id = self.other_plan.id
        self.assertEqual(
            self.tenant._config_sync_vals()['allowed_module_names'], 'stock')

    # ------------------------------------------- what the admin form displays
    def test_the_tenant_form_shows_the_plans_modules(self):
        """`effective_module_names` is what the tenant form renders, and it
        must agree with what is actually sent — a display that can disagree
        with the payload is how an admin is misled."""
        self.plan.allowed_module_names = ' crm , sale , crm '
        self.tenant.invalidate_recordset(['effective_module_names'])
        # Parsed through the plan's own parser: trimmed and de-duplicated,
        # exactly like the list provisioning and sync use.
        self.assertEqual(self.tenant.effective_module_names, 'crm, sale')

    def test_a_tenant_with_no_plan_shows_no_modules_rather_than_failing(self):
        tenant = self.env['ncollection.tenant'].create({
            'company_name': 'Planless Co', 'database_name': 'planless'})
        self.assertEqual(tenant.effective_module_names, '')

    # ------------------------------------------------------- manual re-push
    def test_sync_now_uses_the_same_enqueue_as_an_automatic_push(self):
        """The retry button must not be a second code path — if it were, it
        could succeed where the real one fails (or vice versa)."""
        pushed = self._capture_enqueue()
        self.tenant.action_config_sync_now()
        self.assertEqual(pushed, ['modco'])

    def test_sync_now_refuses_a_tenant_with_no_ready_database(self):
        """Pushing config at a database that does not exist is a mistake worth
        naming, not a silent success."""
        unprovisioned = self.env['ncollection.tenant'].create({
            'company_name': 'Not Yet Co', 'database_name': 'notyetco',
            'plan_id': self.plan.id})
        with self.assertRaises(UserError):
            unprovisioned.action_config_sync_now()

    # --------------------------------------------- the catalog is NOT wiring
    def test_the_module_catalog_model_is_not_loaded_at_all(self):
        """Pinned on purpose (#455), and it pins a stronger fact than expected.

        `ncollection_subscription/models/module.py` defines `ncollection.module`
        with a tenant M2M and a kanban of toggles, and it reads exactly like the
        place an admin would manage a customer's modules. It is DEAD CODE: the
        model is not imported in `models/__init__.py` and `module_views.xml` is
        not in the manifest, so neither the model nor its views ever load. The
        misleading UI therefore does not exist at runtime — which is why the
        fix for this issue is to surface the REAL control (the plan's module
        list) rather than to rewire the catalog.

        If someone loads it later, this test fails and forces that to be a
        deliberate decision — with the admin UI and the docs updated — instead
        of quietly creating a second source of truth beside the plan.
        """
        self.assertNotIn(
            'ncollection.module', self.env.registry.models,
            "ncollection.module is dead code (see module.py). Loading it "
            "re-creates a module-management surface that licenses nothing — "
            "wire it to the plan's allowed_module_names, or leave it unloaded.")

    def test_licensing_comes_only_from_the_plan(self):
        """The positive half of the statement above: the module set a tenant
        receives is a pure function of its plan, with no other contributor."""
        self.plan.allowed_module_names = 'crm,sale'
        self.tenant.invalidate_recordset(['effective_module_names'])
        self.assertEqual(
            self.tenant._config_sync_vals()['allowed_module_names'], 'crm,sale')
        self.assertEqual(self.tenant.effective_module_names, 'crm, sale')
