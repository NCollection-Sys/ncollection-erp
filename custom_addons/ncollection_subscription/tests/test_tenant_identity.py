# -*- coding: utf-8 -*-
"""Tenant identity, addressing and credentials (#476).

Three concepts that were previously two-and-a-half — what a human calls the
tenant, its database, and the host it answers on — plus the administrator
credential, which used to be welded to the contact email.
"""
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestTenantDisplayName(TransactionCase):
    """The breadcrumb bug, pinned.

    Without `_rec_name` Odoo looks for a field called `name`, finds none, and
    `display_name` becomes the literal "ncollection.tenant,5" — which is what
    the breadcrumb, every many2one label and every chatter subject showed.
    """

    def test_a_tenant_displays_as_its_company_name(self):
        tenant = self.env['ncollection.tenant'].create({
            'company_name': 'Atlas Trading LLC', 'database_name': 'atlasdisp'})
        self.assertEqual(tenant.display_name, 'Atlas Trading LLC')

    def test_the_display_name_is_never_the_technical_identifier(self):
        """The control: assert the SHAPE that was leaking, not just the happy
        value, so a future model change cannot quietly bring it back."""
        tenant = self.env['ncollection.tenant'].create({
            'company_name': 'Atlas Trading LLC', 'database_name': 'atlasdisp2'})
        self.assertNotIn('ncollection.tenant,', tenant.display_name)


@tagged('post_install', '-at_install')
class TestTenantAddressing(TransactionCase):
    """subdomain / base domain / URL — three fields, one host."""

    def _tenant(self, **kw):
        vals = {'company_name': 'Atlas', 'database_name': 'atlasaddr'}
        vals.update(kw)
        return self.env['ncollection.tenant'].create(vals)

    def test_the_url_is_built_from_the_subdomain(self):
        tenant = self._tenant(subdomain='atlas')
        self.assertEqual(tenant.base_domain,
                         self.env['ncollection.tenant']._nc_base_domain())
        self.assertTrue(tenant.tenant_url.endswith(
            'atlas.%s' % tenant.base_domain))

    def test_it_falls_back_to_the_database_name(self):
        """The fallback is what makes this safe to add to a platform that
        already has tenants: every one of them keeps the host it had, with no
        migration and no re-provisioning."""
        tenant = self._tenant(database_name='legacydb')
        self.assertFalse(tenant.subdomain)
        self.assertEqual(tenant._nc_subdomain_label(), 'legacydb')
        self.assertIn('legacydb.', tenant.tenant_url)

    def test_a_tenant_with_neither_has_no_url_rather_than_a_broken_one(self):
        tenant = self._tenant(database_name=False)
        self.assertFalse(tenant.tenant_url)

    def test_the_base_domain_comes_from_the_one_parameter(self):
        """Changing the platform setting changes every tenant URL — asserted,
        because the whole point of the field is that it is not per-tenant."""
        self.env['ir.config_parameter'].sudo().set_param(
            'ncollection_saas.base_domain', 'example.test')
        tenant = self._tenant(subdomain='atlas')
        self.assertEqual(tenant.tenant_url, 'https://atlas.example.test')

    def test_the_input_is_normalised_as_it_is_typed(self):
        tenant = self._tenant()
        tenant.subdomain = '  ATLAS  '
        tenant._onchange_subdomain_normalise()
        self.assertEqual(tenant.subdomain, 'atlas')


@tagged('post_install', '-at_install')
class TestTenantAdminCredentials(TransactionCase):
    """The administrator signs in with a USERNAME (#476)."""

    def _tenant(self, **kw):
        vals = {'company_name': 'Atlas', 'database_name': 'atlascred',
                'email': 'atlas@example.test'}
        vals.update(kw)
        return self.env['ncollection.tenant'].create(vals)

    def test_the_login_is_the_username_when_one_is_set(self):
        tenant = self._tenant(admin_login='atlasadmin')
        self.assertEqual(tenant._nc_admin_login(), 'atlasadmin')

    def test_the_login_falls_back_to_the_email(self):
        """Every flow that sets no username — the public checkout, an import —
        must produce exactly the login it produced before this field."""
        self.assertEqual(self._tenant()._nc_admin_login(), 'atlas@example.test')

    def test_a_mistyped_confirmation_is_refused(self):
        """Caught here, where it can still be corrected. Otherwise the customer
        discovers it at first login, against a database already built."""
        with self.assertRaises(ValidationError):
            self._tenant(admin_password='Secret123!',
                         admin_password_confirm='Secret124!')

    def test_a_short_password_is_refused(self):
        with self.assertRaises(ValidationError):
            self._tenant(admin_password='short', admin_password_confirm='short')

    def test_a_matching_password_is_accepted_and_reported_as_pending(self):
        tenant = self._tenant(admin_password='Secret123!',
                              admin_password_confirm='Secret123!')
        self.assertEqual(tenant.admin_credentials_state, 'pending')

    def test_the_password_is_erased_once_the_database_holds_it(self):
        """The platform holds a usable tenant credential for the queue latency,
        not forever."""
        tenant = self._tenant(admin_password='Secret123!',
                              admin_password_confirm='Secret123!')
        tenant._nc_clear_admin_password()
        self.assertFalse(tenant.admin_password)

    def test_no_password_means_the_hardened_default(self):
        """Unset is not a broken state: it is the born-hardened tenant, whose
        owner sets their own password through the reset link."""
        tenant = self._tenant()
        self.assertEqual(tenant.admin_credentials_state, 'reset_link')


@tagged('post_install', '-at_install')
class TestTenantModuleOverrides(TransactionCase):
    """Per-tenant deltas against the plan (#476)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.plan = cls.env['ncollection.subscription.plan'].create({
            'name': 'Ovr', 'code': 'OVRPLAN', 'max_users': 5,
            'allowed_module_names': 'crm,sale,account'})
        cls.other_plan_tenant = cls.env['ncollection.tenant'].create({
            'company_name': 'Beta', 'database_name': 'betaovr',
            'plan_id': cls.plan.id})
        cls.tenant = cls.env['ncollection.tenant'].create({
            'company_name': 'Atlas', 'database_name': 'atlasovr',
            'plan_id': cls.plan.id})

    def _override(self, name, mode='disable'):
        return self.env['ncollection.tenant.module.override'].create({
            'tenant_id': self.tenant.id, 'module_name': name, 'mode': mode})

    def test_disabling_removes_it_from_this_tenants_entitlement(self):
        self._override('crm')
        self.assertNotIn('crm', self.tenant._nc_effective_module_list())
        self.assertIn('sale', self.tenant._nc_effective_module_list())

    def test_the_plan_itself_is_untouched(self):
        """The requirement this exists for: one customer's exception must not
        be a product change."""
        self._override('crm')
        self.assertIn('crm', self.plan.get_allowed_module_list())

    def test_other_tenants_on_the_plan_are_unaffected(self):
        """The control. Every assertion above would also pass if the override
        had simply broken entitlement for everybody."""
        self._override('crm')
        self.assertIn('crm', self.other_plan_tenant._nc_effective_module_list())

    def test_adding_grants_a_module_the_plan_does_not(self):
        self._override('stock', mode='enable')
        self.assertIn('stock', self.tenant._nc_effective_module_list())

    def test_removing_the_override_restores_the_plans_answer_exactly(self):
        """What makes an override safe to undo."""
        before = self.tenant._nc_effective_module_list()
        override = self._override('crm')
        self.assertNotEqual(self.tenant._nc_effective_module_list(), before)
        override.unlink()
        self.assertEqual(self.tenant._nc_effective_module_list(), before)

    def test_one_decision_per_module(self):
        """Two rows for one module would make the result depend on row order."""
        self._override('crm')
        with self.assertRaises(Exception):
            with self.env.cr.savepoint():
                self._override('crm', mode='enable')

    def test_a_module_that_does_not_exist_is_refused(self):
        """A typo is otherwise invisible: a 'disable' for a module nobody has
        silently does nothing."""
        with self.assertRaises(ValidationError):
            self._override('nc_module_that_does_not_exist')

    def test_a_localization_module_cannot_be_disabled(self):
        """It is not a feature the customer bought — it is what makes their
        books legal, and a chart of accounts cannot be un-loaded by revoking
        the licence: the tables stay and the menus vanish."""
        self.tenant.country_id = self.env.ref('base.ae').id
        localization = self.tenant._nc_localization_modules()
        self.assertTrue(localization, "control: no localization package to test")
        self._override(localization[0])
        self.assertIn(localization[0], self.tenant._nc_effective_module_list())

    def test_the_display_field_mirrors_the_authority(self):
        self._override('crm')
        self.assertEqual(
            [m.strip() for m in self.tenant.effective_module_names.split(',')],
            self.tenant._nc_effective_module_list())
