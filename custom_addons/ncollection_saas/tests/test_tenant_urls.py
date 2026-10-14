# -*- coding: utf-8 -*-
"""One authority for where a tenant lives: <subdomain>.<base domain>.

The base domain is configured in ONE place (SaaS Settings, else the NC_BASE_DOMAIN environment of the deployment) and
every address the platform shows or follows is built from it by ONE function, ``_nc_public_origin``. These tests pin
the properties that matter: local development is ``<tenant>.ncollection.localhost``, production is whatever its
domain is, nothing is hardcoded, and a local stack can never fall back to a production domain.
"""
import os
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

BASE = 'ncollection_saas.base_domain'
PORT = 'ncollection_saas.url_port'


@tagged('post_install', '-at_install')
class TestTenantUrls(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Param = cls.env['ir.config_parameter'].sudo()
        cls.Tenant = cls.env['ncollection.tenant']
        cls.tenant = cls.Tenant.sudo().create({'company_name': 'Test Eleven', 'database_name': 'testeleven'})

    def _env_domain(self, value):
        return patch.dict(os.environ, {'NC_BASE_DOMAIN': value})

    # ------------------------------------------------------------------ generation
    def test_local_development_is_a_localhost_subdomain_over_http(self):
        self.Param.set_param(BASE, 'ncollection.localhost')
        self.assertEqual(self.tenant.tenant_url, 'http://testeleven.ncollection.localhost')
        self.assertEqual(self.tenant._nc_host(), 'testeleven.ncollection.localhost')

    def test_production_is_the_configured_domain_over_https(self):
        self.Param.set_param(BASE, 'example.com')
        self.assertEqual(self.tenant.tenant_url, 'https://testeleven.example.com')

    def test_the_tenant_stores_its_identity_not_the_domain(self):
        """Changing the platform domain moves every tenant URL with no change to any tenant record."""
        self.Param.set_param(BASE, 'one.example')
        first = self.tenant.tenant_url
        self.Param.set_param(BASE, 'two.example')
        self.tenant.invalidate_recordset()
        self.assertNotEqual(first, self.tenant.tenant_url)
        self.assertEqual(self.tenant.database_name, 'testeleven')
        self.assertFalse(self.Tenant._fields['tenant_url'].store)

    def test_an_edited_subdomain_is_the_host_label(self):
        self.Param.set_param(BASE, 'example.com')
        self.tenant.subdomain = 'eleven'
        self.assertEqual(self.tenant.tenant_url, 'https://eleven.example.com')

    def test_a_port_is_added_only_when_one_is_configured(self):
        self.Param.set_param(BASE, 'ncollection.localhost')
        self.assertNotIn(':', self.tenant.tenant_url.split('//')[1])
        self.Param.set_param(PORT, '8069')
        self.tenant.invalidate_recordset()
        self.assertEqual(self.tenant.tenant_url, 'http://testeleven.ncollection.localhost:8069')
        self.Param.set_param(PORT, 'not-a-port')
        self.tenant.invalidate_recordset()
        self.assertNotIn('not-a-port', self.tenant.tenant_url)

    # ------------------------------------------------------------------ one authority, one order, no fallback
    def test_the_setting_wins_over_the_deployment_environment(self):
        self.Param.set_param(BASE, 'settings.example')
        with self._env_domain('env.example'):
            self.assertEqual(self.Tenant._nc_base_domain(), 'settings.example')

    def test_the_deployment_environment_is_used_when_the_setting_is_empty(self):
        self.Param.set_param(BASE, '')
        with self._env_domain('ncollection.localhost'):
            self.assertEqual(self.Tenant._nc_base_domain(), 'ncollection.localhost')
            self.assertEqual(self.tenant.tenant_url, 'http://testeleven.ncollection.localhost')

    def test_with_nothing_configured_there_is_no_url_and_never_a_production_default(self):
        self.Param.set_param(BASE, '')
        with self._env_domain(''):
            self.assertEqual(self.Tenant._nc_base_domain(), '')
            self.assertFalse(self.tenant.tenant_url)
            self.assertFalse(self.tenant._nc_public_origin())
            with self.assertRaises(UserError):
                self.Tenant._nc_require_base_domain()

    def test_a_local_stack_never_produces_a_production_domain(self):
        self.Param.set_param(BASE, '')
        with self._env_domain('ncollection.localhost'):
            self.tenant.invalidate_recordset()
            produced = [self.tenant.tenant_url, self.tenant._nc_host(), self.tenant._nc_public_origin(),
                        self.env['ncollection.domain']._fqdn_for_tenant(self.tenant)]
        self.assertTrue(all(produced), produced)
        for value in produced:
            self.assertNotIn('ncollectionerp', value)

    def test_the_seeded_production_value_is_gone_from_new_databases(self):
        """Nothing seeds a base domain any more: the parameter exists only when an operator set one."""
        self.assertFalse(self.env['ir.model.data'].sudo().search(
            [('module', '=', 'ncollection_saas'), ('name', '=', 'param_base_domain')]))

    # ------------------------------------------------------------------ every consumer agrees
    def test_company_entry_portal_and_fqdn_all_come_from_the_builder(self):
        self.Param.set_param(BASE, 'ncollection.localhost')
        origin = self.tenant.tenant_url
        entry = self.tenant._nc_entry_url('tok')
        self.assertTrue(entry.startswith(origin + '/ncollection/platform/enter#token='), entry)
        job = self.env['ncollection.provisioning.job'].sudo().create({
            'tenant_id': self.tenant.id, 'database_name': self.tenant.database_name})
        self.assertEqual(job._portal_url('testeleven'), origin)
        self.assertEqual(self.env['ncollection.domain']._fqdn_for_tenant(self.tenant), self.tenant._nc_host())

    def test_the_signup_login_link_is_the_builder_plus_the_backend_path(self):
        self.Param.set_param(BASE, 'ncollection.localhost')
        self.assertEqual(self.tenant._nc_public_origin() + '/odoo', 'http://testeleven.ncollection.localhost/odoo')

    # ------------------------------------------------------------------ the legacy snapshot
    def test_the_retired_dev_template_value_of_portal_url_is_repaired_and_only_that(self):
        self.Param.set_param(BASE, 'ncollection.localhost')
        stale = self.Tenant.sudo().create({'company_name': 'Stale', 'database_name': 'staleone',
                                           'portal_url': 'http://staleone.localhost:8069'})
        typed = self.Tenant.sudo().create({'company_name': 'Typed', 'database_name': 'typedone',
                                           'portal_url': 'https://operator.example/typed'})
        fixed = self.Tenant._nc_repair_legacy_portal_urls()
        self.assertIn(stale, fixed)
        self.assertEqual(stale.portal_url, 'http://staleone.ncollection.localhost')
        self.assertEqual(stale.portal_url, stale.tenant_url)
        self.assertNotIn(typed, fixed)
        self.assertEqual(typed.portal_url, 'https://operator.example/typed')
        self.assertFalse(self.Tenant._nc_repair_legacy_portal_urls() & (stale | typed), "idempotent")

    def test_nothing_is_rewritten_when_no_base_domain_is_known(self):
        self.Param.set_param(BASE, '')
        stale = self.Tenant.sudo().create({'company_name': 'Stale2', 'database_name': 'staletwo',
                                           'portal_url': 'http://staletwo.localhost:8069'})
        with self._env_domain(''):
            self.assertFalse(self.Tenant._nc_repair_legacy_portal_urls() & stale)
        self.assertEqual(stale.portal_url, 'http://staletwo.localhost:8069')
