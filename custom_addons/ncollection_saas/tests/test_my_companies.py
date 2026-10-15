# -*- coding: utf-8 -*-
"""A company a platform administrator creates reaches that administrator's "My Companies", at every stage.

Regression for test11: the platform form posts ``platform_admin_ids`` EMPTY on create, the "creator may enter it"
default only applied when the key was absent, and a company that was created, provisioned and ready was listed for
nobody. These tests go through the same create call the form makes.
"""
from odoo.tests import TransactionCase, tagged

PLATFORM_GROUP = 'ncollection_subscription.group_platform_admin'


@tagged('post_install', '-at_install')
class TestMyCompanies(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['ir.config_parameter'].sudo().set_param('ncollection_saas.base_domain', 'ncollection.localhost')
        cls.Tenant = cls.env['ncollection.tenant']
        cls.Summary = cls.env['ncollection.subscription.dashboard']
        internal, group = cls.env.ref('base.group_user'), cls.env.ref(PLATFORM_GROUP)
        system = cls.env.ref('base.group_system')
        make = cls.env['res.users'].with_context(no_reset_password=True).create
        cls.alice = make({'name': 'Alice', 'login': 'mc_alice',
                          'group_ids': [(6, 0, [internal.id, group.id, system.id])]})
        cls.bob = make({'name': 'Bob', 'login': 'mc_bob',
                        'group_ids': [(6, 0, [internal.id, group.id, system.id])]})
        cls.clerk = make({'name': 'Clerk', 'login': 'mc_clerk', 'group_ids': [(6, 0, [internal.id])]})

    def _create(self, user, db, **extra):
        """The call the platform form makes: the allow-list is posted, empty."""
        return self.Tenant.with_user(user).create(dict(
            {'company_name': db.title(), 'database_name': db, 'platform_admin_ids': [(6, 0, [])]}, **extra))

    def _mine(self, user):
        return self.Tenant.with_user(user)._nc_my_companies_for()

    def _summary_ids(self, user):
        return self.Summary.with_user(user).create({}).enterable_tenant_ids

    # ------------------------------------------------------------------ the regression
    def test_a_company_created_from_the_form_is_listed_for_its_creator(self):
        tenant = self._create(self.alice, 'mctest11')
        self.assertEqual(tenant.platform_admin_ids, self.alice, "the creator is authorised, not nobody")
        self.assertIn(tenant, self._mine(self.alice), "created -> My Companies, without any manual database change")

    def test_every_shape_of_an_empty_allow_list_gets_the_creator(self):
        for index, posted in enumerate(([], [(6, 0, [])], [(5, 0, 0)], None)):
            vals = {'company_name': 'S%d' % index, 'database_name': 'mcshape%d' % index}
            if posted is not None:
                vals['platform_admin_ids'] = posted
            tenant = self.Tenant.with_user(self.alice).create(vals)
            self.assertEqual(tenant.platform_admin_ids, self.alice, posted)

    def test_an_explicit_allow_list_is_respected_and_does_not_add_the_creator(self):
        tenant = self.Tenant.with_user(self.alice).create({
            'company_name': 'Named', 'database_name': 'mcnamed', 'platform_admin_ids': [(6, 0, [self.bob.id])]})
        self.assertEqual(tenant.platform_admin_ids, self.bob)

    # ------------------------------------------------------------------ who sees it
    def test_another_owner_does_not_see_it(self):
        tenant = self._create(self.alice, 'mcprivate')
        self.assertNotIn(tenant, self._mine(self.bob))
        self.assertNotIn(tenant, self._summary_ids(self.bob))
        self.assertIn(tenant, self._summary_ids(self.alice))

    def test_a_non_platform_user_sees_nothing(self):
        self._create(self.alice, 'mcnone')
        self.assertFalse(self._mine(self.clerk))

    def test_ownership_is_not_inferred_from_other_tenants(self):
        a, b = self._create(self.alice, 'mcalpha'), self._create(self.bob, 'mcbeta')
        self.assertEqual((a.platform_admin_ids, b.platform_admin_ids), (self.alice, self.bob))
        self.assertEqual(self._mine(self.alice) & (a | b), a)
        self.assertEqual(self._mine(self.bob) & (a | b), b)

    # ------------------------------------------------------------------ lifecycle, URL, entry
    def test_a_company_that_is_still_being_built_is_listed_with_its_stage_but_cannot_be_entered(self):
        tenant = self._create(self.alice, 'mcbuild')
        tenant.sudo().write({'database_status': 'provisioning'})
        self.assertIn(tenant, self._mine(self.alice), "created and building is visible, not missing")
        self.assertNotIn(tenant, self.Tenant.with_user(self.alice)._nc_enterable_for(), "but not enterable yet")
        row = self._summary_ids(self.alice).filtered(lambda t: t == tenant)
        self.assertEqual(row.provision_stage, 'provisioning')

    def test_a_ready_company_is_enterable_and_shows_the_one_url(self):
        tenant = self._create(self.alice, 'mcready')
        tenant.sudo().write({'database_status': 'ready'})
        self.assertIn(tenant, self.Tenant.with_user(self.alice)._nc_enterable_for())
        self.assertEqual(tenant.tenant_url, 'http://mcready.ncollection.localhost')
        self.assertEqual(tenant.provision_stage, 'ready')

    def test_a_failed_build_is_listed_so_it_is_not_silently_lost(self):
        tenant = self._create(self.alice, 'mcfail')
        tenant.sudo().write({'database_status': 'error'})
        self.assertIn(tenant, self._mine(self.alice))
        self.assertNotIn(tenant, self.Tenant.with_user(self.alice)._nc_enterable_for())

    def test_a_suspended_company_is_not_offered(self):
        tenant = self._create(self.alice, 'mcgone')
        tenant.sudo().write({'database_status': 'ready', 'status': 'active'})
        tenant.sudo().write({'status': 'suspended'})
        self.assertNotIn(tenant, self._mine(self.alice))
