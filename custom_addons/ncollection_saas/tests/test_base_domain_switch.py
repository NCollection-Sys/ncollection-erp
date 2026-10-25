# -*- coding: utf-8 -*-
"""Changing the configured base domain changes EVERY generated tenant address, with no change to any code.

A new tenant is created the way every creation path creates it (the tenant record, then its provisioning job), under
three different base domains, and each address the platform builds for it is read back: the tenant URL, the Company
Entry URL and its service-channel Host, the provisioning portal URL, the value handed to the seed (which becomes the
tenant's own ``web.base.url``, so its password-reset and verification links), the domain record's FQDN and the sign-in
link of the signup flow. Each must be ``<label>.<base domain>`` for the CURRENT domain and name no other domain.
``balancecloud.ae`` is the production domain this was prepared for; the development default and a third unrelated
domain prove that nothing in the code knows any of them.
"""
import os
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

from odoo.addons.ncollection_core.app_url import app_url

BASE = 'ncollection_saas.base_domain'
DOMAINS = (
    ('ncollection.localhost', 'http'),      # the development default: loopback, so plain http
    ('balancecloud.ae', 'https'),           # the production domain this is prepared for
    ('example.org', 'https'),               # any other: nothing is special about the two above
)


@tagged('post_install', '-at_install')
class TestBaseDomainSwitch(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Param = cls.env['ir.config_parameter'].sudo()
        cls.Tenant = cls.env['ncollection.tenant']
        cls.tenant = cls.Tenant.sudo().create({
            'company_name': 'Domain Switch Co', 'database_name': 'domainswitch', 'email': 'owner@domainswitch.example',
            'status': 'trial'})
        cls.job = cls.env['ncollection.provisioning.job'].sudo().create({
            'tenant_id': cls.tenant.id, 'database_name': 'domainswitch', 'status': 'queued'})

    def _seed_portal_url(self):
        """The ``NC_PORTAL_URL`` the provisioning seed is handed (the tenant's own web.base.url comes from it)."""
        captured = {}

        def fake_run(_self, cmd, label, stdin=None, env=None, timeout=None):
            captured['env'] = env
            return ''

        with patch.object(type(self.job), '_run_odoo_subprocess', new=fake_run):
            self.job._seed_tenant('domainswitch')
        return captured['env']['NC_PORTAL_URL']

    def _addresses(self):
        """Every address generated for the tenant, by the code path that generates it."""
        self.tenant.invalidate_recordset()
        entry = self.tenant._nc_entry_url('tok')
        return {
            'tenant_url': self.tenant.tenant_url,
            'entry_origin': entry.split('/ncollection/platform/enter#token=')[0],
            'portal_url': self.job._portal_url('domainswitch'),
            'seed_portal_url': self._seed_portal_url(),
            'fqdn': self.env['ncollection.domain']._fqdn_for_tenant(self.tenant),
            'service_host': self.Tenant._nc_service_host('domainswitch'),
            'signup_login': self.tenant._nc_public_origin() + app_url(),
        }

    def test_each_domain_produces_its_own_addresses_everywhere(self):
        for domain, scheme in DOMAINS:
            with self.subTest(domain=domain):
                self.Param.set_param(BASE, domain)
                generated = self._addresses()
                origin = '%s://domainswitch.%s' % (scheme, domain)
                self.assertEqual(generated['tenant_url'], origin)
                self.assertEqual(generated['entry_origin'], origin, 'Company Entry uses the one builder')
                self.assertEqual(generated['portal_url'], origin)
                self.assertEqual(generated['seed_portal_url'], origin, "the tenant's web.base.url follows the domain")
                self.assertEqual(generated['fqdn'], 'domainswitch.' + domain)
                self.assertEqual(generated['service_host'], 'domainswitch.' + domain)
                self.assertEqual(generated['signup_login'], origin + '/atlas')

    def test_switching_the_domain_moves_every_address_and_leaves_no_trace_of_the_previous_one(self):
        seen = {}
        for domain, _scheme in DOMAINS:
            self.Param.set_param(BASE, domain)
            seen[domain] = self._addresses()
        for domain, generated in seen.items():
            for name, value in generated.items():
                for other, _s in DOMAINS:
                    if other != domain:
                        self.assertNotIn(other, value,
                                         '%s still names %s after the domain became %s' % (name, other, domain))

    def test_the_domain_comes_from_the_deployment_when_no_setting_overrides_it(self):
        """Production sets DOMAIN / NC_BASE_DOMAIN in the environment and never touches SaaS Settings."""
        self.Param.set_param(BASE, '')
        with patch.dict(os.environ, {'NC_BASE_DOMAIN': 'balancecloud.ae'}):
            generated = self._addresses()
        expected = {'domainswitch.balancecloud.ae', 'https://domainswitch.balancecloud.ae',
                    'https://domainswitch.balancecloud.ae/atlas'}
        self.assertEqual(set(generated.values()) - expected, set())

    def test_the_setting_wins_over_the_deployment_so_one_value_decides(self):
        self.Param.set_param(BASE, 'example.org')
        with patch.dict(os.environ, {'NC_BASE_DOMAIN': 'balancecloud.ae'}):
            self.assertEqual(self.tenant._nc_public_origin(), 'https://domainswitch.example.org')

    def test_a_tenant_can_never_take_the_label_of_the_platforms_own_apex_database(self):
        """Under db_filter ^%d$ the apex balancecloud.ae selects the database 'balancecloud': always reserved."""
        self.Param.set_param(BASE, 'balancecloud.ae')
        with patch('odoo.addons.ncollection_saas.models.provisioning_job.ProvisioningJob._database_exists',
                   return_value=False):
            ok, reason = self.Tenant._nc_subdomain_availability('balancecloud')
            self.assertEqual((ok, reason), (False, 'reserved'))
            self.assertTrue(self.Tenant._nc_subdomain_availability('acmetrading')[0])
            self.Param.set_param(BASE, 'example.org')       # the reservation follows the configured domain
            self.assertTrue(self.Tenant._nc_subdomain_availability('balancecloud')[0])
            self.assertEqual(self.Tenant._nc_subdomain_availability('example'), (False, 'reserved'))
