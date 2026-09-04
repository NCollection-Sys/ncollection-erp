# -*- coding: utf-8 -*-
"""Country-driven localization: the platform-side half (#469).

What this covers is the DECISION — which package a tenant resolves to, which
modules that adds to its entitlement, and that those modules can never be
picked in a plan. Whether a tenant database actually ended up on the UAE chart
is proven in ncollection_saas (the provisioning half) and by the live
verification in the PR, because it needs a real database.
"""
from odoo.tests import TransactionCase, tagged

from ..models.localization import (
    LOCALIZATION_PACKAGES, PLAN_EXCLUDED_MODULES, localization_package)


@tagged('post_install', '-at_install')
class TestLocalizationPackages(TransactionCase):

    def test_the_uae_package_names_a_chart_and_a_currency(self):
        """The package is the VERIFICATION TARGET, not a hint: provisioning
        fails a job whose tenant did not end up on exactly these."""
        package = localization_package('AE')
        self.assertEqual(package['chart_template'], 'ae')
        self.assertEqual(package['currency'], 'AED')
        self.assertIn('l10n_ae', package['modules'])
        self.assertIn('ncollection_account_localization_uae', package['modules'])

    def test_the_lookup_is_case_insensitive(self):
        """`res.country.code` is upper-case, but a code reaching this from a
        payload or an import may not be — and silently returning None would
        provision a UAE tenant with no localization at all."""
        self.assertEqual(localization_package('ae'), localization_package('AE'))

    def test_an_unknown_or_empty_country_is_a_normal_no_package(self):
        """Not an error: a tenant in a country we ship no package for
        provisions exactly as every tenant did before #469."""
        self.assertIsNone(localization_package('ZZ'))
        self.assertIsNone(localization_package(''))
        self.assertIsNone(localization_package(None))

    def test_every_package_declares_what_the_engine_reads(self):
        """A package missing a key would fail deep inside provisioning, on a
        real customer's database, rather than here."""
        for code, package in LOCALIZATION_PACKAGES.items():
            for key in ('name', 'modules', 'chart_template', 'currency'):
                self.assertIn(key, package, "package %s lacks %r" % (code, key))
            self.assertTrue(package['modules'])

    def test_the_excluded_list_is_exactly_every_packages_modules(self):
        """Derived, never hand-maintained: adding a country must not silently
        leave its l10n module selectable in the plan picker."""
        expected = {m for p in LOCALIZATION_PACKAGES.values() for m in p['modules']}
        self.assertEqual(set(PLAN_EXCLUDED_MODULES), expected)


@tagged('post_install', '-at_install')
class TestTenantEffectiveModules(TransactionCase):
    """The tenant's entitlement is plan UNION localization — in ONE place.

    Four readers answered this question independently before #469
    (provisioning, module install, config sync, the display field). Adding the
    country as a second SOURCE to four separate readers is how a module ends up
    installed but unlicensed, or licensed but never installed (#461).
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.plan = cls.env['ncollection.subscription.plan'].create({
            'name': 'Loc Plan', 'code': 'LOCPLAN', 'max_users': 5,
            'allowed_module_names': 'crm,sale'})
        cls.ae = cls.env.ref('base.ae')

    _seq = 0

    def _tenant(self, **kw):
        # Unique per call: database_name carries a UNIQUE constraint (#225), so
        # two fixtures sharing one name is an integrity error, not a test.
        type(self)._seq += 1
        vals = {'company_name': 'Loc Co', 'plan_id': self.plan.id,
                'database_name': 'loctest%d' % self._seq}
        vals.update(kw)
        return self.env['ncollection.tenant'].create(vals)

    def test_a_uae_tenant_is_entitled_to_the_package_on_top_of_its_plan(self):
        tenant = self._tenant(country_id=self.ae.id)
        modules = tenant._nc_effective_module_list()
        self.assertIn('crm', modules)
        self.assertIn('l10n_ae', modules)
        self.assertIn('base_vat', modules)
        self.assertIn('ncollection_account_localization_uae', modules)

    def test_a_tenant_with_no_country_gets_exactly_its_plan(self):
        """The pre-#469 behaviour, preserved: no country must not mean a
        surprise localization."""
        tenant = self._tenant()
        self.assertEqual(tenant._nc_effective_module_list(), ['crm', 'sale'])
        self.assertEqual(tenant._nc_localization_modules(), [])

    def test_a_country_with_no_package_gets_exactly_its_plan(self):
        tenant = self._tenant(country_id=self.env.ref('base.fr').id)
        self.assertEqual(tenant._nc_effective_module_list(), ['crm', 'sale'])

    def test_a_plan_that_already_names_a_localization_module_is_not_duplicated(self):
        """A platform whose ENTERPRISE plan still names the UAE module from
        before #469 must not install or license it twice."""
        self.plan.allowed_module_names = 'crm,ncollection_account_localization_uae'
        tenant = self._tenant(country_id=self.ae.id)
        modules = tenant._nc_effective_module_list()
        self.assertEqual(modules.count('ncollection_account_localization_uae'), 1)

    def test_the_display_field_mirrors_the_authority(self):
        tenant = self._tenant(country_id=self.ae.id)
        self.assertEqual(
            [m.strip() for m in tenant.effective_module_names.split(',')],
            tenant._nc_effective_module_list())

    def test_the_status_field_says_which_package_applies(self):
        self.assertIn('ae', self._tenant(country_id=self.ae.id).localization_status)
        self.assertTrue(self._tenant().localization_status)


@tagged('post_install', '-at_install')
class TestLocalizationIsNotPlanSelectable(TransactionCase):
    """A localization module must never appear in the plan module picker.

    It is not a feature an operator buys — it is what makes the tenant's books
    legal. Offering it would let someone deselect a live tenant's chart of
    accounts (which cannot be un-loaded by re-ticking the box) or select the
    wrong country's for a tenant.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Plan = cls.env['ncollection.subscription.plan']
        cls.optional_names = {
            m['name'] for m in cls.Plan.get_selectable_modules()['optional']}

    def test_no_localization_module_is_offered(self):
        for name in PLAN_EXCLUDED_MODULES:
            self.assertNotIn(name, self.optional_names,
                             "%s is a localization module and must not be "
                             "selectable in a plan (#469)" % name)

    def test_the_control_the_picker_still_offers_business_modules(self):
        """Without this, an empty catalog would satisfy the assertion above."""
        self.assertTrue(self.optional_names)
        self.assertIn('ncollection_account_reports', self.optional_names)

    def test_the_uae_module_is_present_on_this_platform(self):
        """The control for the exclusion: if the module were simply absent, the
        assertion above would pass while proving nothing."""
        self.assertTrue(self.env['ir.module.module'].sudo().search_count(
            [('name', '=', 'ncollection_account_localization_uae')]))
