# -*- coding: utf-8 -*-
"""Custom tenant domains, platform side: validation, authorisation, verification, the edge contract, primary switch."""
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged

from odoo.addons.ncollection_saas.controllers.edge import derive_edge_key
from odoo.addons.ncollection_saas.models.config_sync import derive_tenant_key
from odoo.addons.ncollection_saas.models.domain import normalize_fqdn
from odoo.addons.ncollection_saas.tools import dnsquery

EDGE_V4, EDGE_V6 = '203.0.113.10', '2001:db8::10'


class FakeResolver:
    DnsError = dnsquery.DnsError

    def __init__(self, txt=(), v4=(), v6=(), cname='', fail=False):
        self.txt, self.v4, self.v6, self.cname, self.fail = list(txt), set(v4), set(v6), cname, fail

    def txt_values(self, name):
        if self.fail:
            raise dnsquery.DnsError('timeout')
        return self.txt

    def addresses(self, name):
        return self.v4, self.v6, self.cname


@tagged('post_install', '-at_install')
class TestCustomDomain(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        params = cls.env['ir.config_parameter'].sudo()
        params.set_param('ncollection_saas.provisioning_quota_per_hour', '0')
        params.set_param('ncollection_saas.base_domain', 'platform.example')
        params.set_param('ncollection_saas.custom_domains_enabled', 'True')
        params.set_param('ncollection_saas.custom_domain_ipv4', EDGE_V4)
        params.set_param('ncollection_saas.custom_domain_ipv6', EDGE_V6)
        params.set_param('ncollection_saas.custom_domain_cname', 'edge.platform.example')
        cls.plan = cls.env['ncollection.subscription.plan'].create({
            'name': 'Dom', 'code': 'CUSTDOM', 'allowed_module_names': 'crm', 'max_users': 5})
        Users = cls.env['res.users'].with_context(no_reset_password=True)
        platform = cls.env.ref('ncollection_subscription.group_platform_admin')
        cls.padmin = Users.create({'name': 'Platform Admin', 'login': 'dom.padmin',
                                   'group_ids': [(6, 0, [cls.env.ref('base.group_user').id, platform.id,
                                                         cls.env.ref('base.group_system').id])]})
        cls.other_padmin = Users.create({'name': 'Other Platform Admin', 'login': 'dom.padmin2',
                                         'group_ids': [(6, 0, [cls.env.ref('base.group_user').id, platform.id,
                                                               cls.env.ref('base.group_system').id])]})
        cls.plain = Users.create({'name': 'Plain', 'login': 'dom.plain',
                                  'group_ids': [(6, 0, [cls.env.ref('base.group_user').id,
                                                        cls.env.ref('base.group_system').id])]})
        cls.tenant = cls._tenant('acme')
        cls.other = cls._tenant('globex')
        cls.Domain = cls.env['ncollection.domain']
        cls.Domain._sync_for_tenant(cls.tenant)
        cls.Domain._sync_for_tenant(cls.other)

    @classmethod
    def _tenant(cls, db):
        tenant = cls.env['ncollection.tenant'].create({
            'company_name': db.title(), 'plan_id': cls.plan.id, 'status': 'active',
            'database_status': 'ready', 'database_name': db})
        tenant.platform_admin_ids = [(4, cls.padmin.id)]
        return tenant

    def _add(self, fqdn='erp.customer.com', tenant=None, user=None):
        return self.Domain.with_user(user or self.padmin).create({
            'tenant_id': (tenant or self.tenant).id, 'domain_type': 'custom', 'fqdn': fqdn})

    def _verified(self, fqdn='erp.customer.com'):
        domain = self._add(fqdn)
        token = domain.sudo().verification_token
        domain.sudo()._internal()._nc_verify_once(domain._edge_targets(), FakeResolver(
            txt=['nc-verify=' + token], v4=[EDGE_V4]))
        return domain

    # ---------------------------------------------------------------- validation
    def test_hostname_normalisation_and_refusals(self):
        self.assertEqual(normalize_fqdn(' ERP.Customer.COM. '), 'erp.customer.com')
        self.assertEqual(normalize_fqdn('bücher.example.de'), 'xn--bcher-kva.example.de')
        for bad in ('localhost', 'intranet', '10.0.0.1', '::1', 'https://erp.x.com', 'erp.x.com/path',
                    'erp.x.com:8443',
                    '*.customer.com', '-bad.customer.com', 'a..b.com', 'printer.local', 'x.' * 130 + 'com'):
            with self.assertRaises(ValueError, msg=bad):
                normalize_fqdn(bad)

    def test_new_custom_domain_starts_unverified_with_a_strong_token_and_instructions(self):
        domain = self._add()
        self.assertEqual(domain.state, 'pending_verification')
        self.assertFalse(domain.is_primary)
        token = domain.sudo().verification_token
        self.assertGreaterEqual(len(token), 40)
        self.assertNotEqual(token, self._add('portal.customer.com').sudo().verification_token)
        self.assertEqual(domain.verification_record, '_nc-verify.erp.customer.com')
        self.assertIn(EDGE_V4, domain.dns_instructions)
        self.assertIn('edge.platform.example', domain.dns_instructions)

    def test_apex_instructions_never_offer_a_cname_record(self):
        domain = self._add('customer.com')
        self.assertNotIn('<code>CNAME</code>', domain.dns_instructions)
        self.assertIn('<code>A</code>', domain.dns_instructions)

    def test_platform_namespace_duplicates_and_disabled_feature_are_refused(self):
        for fqdn in ('platform.example', 'globex.platform.example', 'x.y.platform.example'):
            with self.assertRaises(ValidationError, msg=fqdn):
                self._add(fqdn)
        self._add()
        with self.assertRaises(Exception):
            with self.env.cr.savepoint():
                self._add('ERP.customer.com', tenant=self.other)      # same host, another tenant
        self.env['ir.config_parameter'].sudo().set_param('ncollection_saas.custom_domains_enabled', 'False')
        with self.assertRaises(UserError):
            self._add('portal.customer.com')

    # ---------------------------------------------------------------- authorisation (ORM, not view)
    def test_only_an_allow_listed_platform_administrator_manages_domains(self):
        with self.assertRaises(AccessError):
            self._add(user=self.plain)
        with self.assertRaises(AccessError):
            self._add(user=self.other_padmin)
        domain = self._add()
        for user in (self.plain, self.other_padmin):
            with self.assertRaises(AccessError):
                domain.with_user(user).action_verify()
            with self.assertRaises(AccessError):
                domain.with_user(user).action_disable()

    def test_status_token_primary_and_target_cannot_be_written_directly(self):
        domain = self._add()
        for vals in ({'state': 'active'}, {'is_primary': True}, {'verification_token': 'x'},
                     {'fqdn': 'evil.attacker.com'}, {'tenant_id': self.other.id}, {'ssl_expiry': '2099-01-01'}):
            with self.assertRaises(AccessError, msg=vals):
                domain.with_user(self.padmin).write(vals)
        domain.with_user(self.padmin).write({'redirect_policy': 'redirect'})   # the one editable choice
        self.assertEqual(domain.redirect_policy, 'redirect')

    def test_subdomains_are_not_created_or_removed_by_hand(self):
        with self.assertRaises(AccessError):
            self.Domain.with_user(self.padmin).create({'tenant_id': self.tenant.id, 'fqdn': 'x.platform.example'})
        subdomain = self.tenant.domain_ids.filtered(lambda d: d.domain_type == 'subdomain')
        self.assertTrue(subdomain.is_primary)
        with self.assertRaises(UserError):
            subdomain.with_user(self.padmin).unlink()

    # ---------------------------------------------------------------- verification
    def test_verification_needs_the_token_and_dns_that_reaches_only_this_edge(self):
        domain = self._add()
        token = domain.sudo().verification_token
        targets = domain._edge_targets()
        run = domain.sudo()._internal()._nc_verify_once
        self.assertFalse(run(targets, FakeResolver(txt=['nc-verify=wrong'], v4=[EDGE_V4])))
        self.assertEqual((domain.state, domain.dns_status), ('pending_verification', 'missing'))
        self.assertFalse(run(targets, FakeResolver(txt=['nc-verify=' + token], v4=['198.51.100.7'])))
        self.assertEqual((domain.state, domain.dns_status), ('dns_required', 'wrong'))
        self.assertFalse(run(targets, FakeResolver(txt=['nc-verify=' + token], v4=[EDGE_V4], v6=['2001:db8::bad'])))
        self.assertEqual(domain.state, 'dns_required', 'a stray AAAA would break issuance and route users elsewhere')
        self.assertFalse(run(targets, FakeResolver(fail=True)))
        self.assertEqual((domain.state, domain.dns_status), ('dns_required', 'error'))
        self.assertTrue(run(targets, FakeResolver(txt=['nc-verify=' + token], v4=[EDGE_V4], v6=[EDGE_V6])))
        self.assertEqual((domain.state, domain.dns_status), ('ssl_pending', 'ok'))

    def test_cname_to_the_edge_target_is_accepted(self):
        domain = self._add('portal.customer.com')
        token = domain.sudo().verification_token
        self.assertTrue(domain.sudo()._internal()._nc_verify_once(domain._edge_targets(), FakeResolver(
            txt=['nc-verify=' + token], v4=[EDGE_V4], cname='edge.platform.example')))

    # ---------------------------------------------------------------- the edge contract
    def test_edge_sees_only_verified_domains_and_the_token_only_while_ssl_is_pending(self):
        self._add('pending.customer.com')
        verified = self._verified()
        state = self.Domain._nc_edge_state()
        names = {row['fqdn']: row for row in state['domains']}
        self.assertEqual(set(names), {'erp.customer.com'})
        self.assertEqual(names['erp.customer.com']['database'], 'acme')
        self.assertEqual(names['erp.customer.com']['token'], verified.sudo().verification_token)
        self.assertEqual(self.Domain._nc_edge_report([{'fqdn': 'erp.customer.com', 'ok': True,
                                                       'expiry': '2099-02-17', 'issuer': 'LE'}]), 1)
        self.assertEqual(verified.state, 'active')
        self.assertFalse(verified.sudo().verification_token, 'the proof is not kept once the domain is active')
        self.assertIsNone(self.Domain._nc_edge_state()['domains'][0]['token'])

    def test_edge_reports_cannot_activate_an_unverified_or_unknown_domain(self):
        pending = self._add()
        self.assertEqual(self.Domain._nc_edge_report([
            {'fqdn': 'erp.customer.com', 'ok': True, 'expiry': '2099-01-01'},
            {'fqdn': 'nobody.example.org', 'ok': True, 'expiry': '2099-01-01'},
            {'fqdn': '<script>', 'ok': True}, 'garbage']), 0)
        self.assertEqual(pending.state, 'pending_verification')
        self.assertFalse(self.Domain.search([('fqdn', '=', 'nobody.example.org')]))

    def test_issuance_failure_is_kept_pending_until_the_edge_says_it_is_final(self):
        domain = self._verified()
        self.Domain._nc_edge_report([{'fqdn': domain.fqdn, 'ok': False, 'error': 'CA timeout', 'final': False}])
        self.assertEqual(domain.state, 'ssl_pending')
        self.assertIn('CA timeout', domain.error_detail)
        self.Domain._nc_edge_report([{'fqdn': domain.fqdn, 'ok': False, 'error': 'rate limited', 'final': True}])
        self.assertEqual(domain.state, 'failed')
        token = domain.sudo().verification_token
        self.assertTrue(domain.sudo()._internal()._nc_verify_once(domain._edge_targets(), FakeResolver(
            txt=['nc-verify=' + token], v4=[EDGE_V4])), 'a failed domain can be verified again')

    def test_edge_key_is_not_the_config_sync_key(self):
        self.assertNotEqual(derive_edge_key('m', 'balancecloud'), derive_tenant_key('m', 'balancecloud'))
        self.assertNotEqual(derive_edge_key('m', 'balancecloud'), derive_edge_key('m', 'atlas-core'))

    # ---------------------------------------------------------------- primary address, disable, remove
    def _activate(self):
        domain = self._verified()
        self.Domain._nc_edge_report([{'fqdn': domain.fqdn, 'ok': True, 'expiry': '2099-02-17'}])
        return domain

    def test_primary_switch_tells_the_tenant_first_and_rolls_back_on_refusal(self):
        domain = self._activate()
        subdomain = self.tenant.domain_ids.filtered(lambda d: d.domain_type == 'subdomain')
        Tenant = type(self.env['ncollection.tenant'])
        with patch.object(Tenant, '_nc_admin_call', autospec=True, side_effect=UserError('unreachable')):
            with self.assertRaises(UserError):
                domain.with_user(self.padmin).action_set_primary()
        self.assertTrue(subdomain.is_primary)
        self.assertFalse(domain.is_primary)
        calls = []
        with patch.object(Tenant, '_nc_admin_call', autospec=True,
                          side_effect=lambda rec, method, payload: calls.append((method, payload)) or {'ok': True}):
            domain.write({'redirect_policy': 'redirect'})
            domain.with_user(self.padmin).action_set_primary()
        self.assertEqual(calls, [('set_canonical_host', {
            'host': 'erp.customer.com', 'redirect': True, 'aliases': ['acme.platform.example', 'erp.customer.com']})])
        self.assertTrue(domain.is_primary)
        self.assertFalse(subdomain.is_primary)
        self.assertEqual(self.tenant.primary_address, 'erp.customer.com')
        self.assertEqual(self.tenant.address_mode, 'custom')

    def test_a_pending_domain_cannot_become_primary(self):
        with self.assertRaises(UserError):
            self._add().with_user(self.padmin).action_set_primary()

    def test_disabling_the_primary_falls_back_to_the_subdomain_and_removal_keeps_the_tenant(self):
        domain = self._activate()
        Tenant = type(self.env['ncollection.tenant'])
        calls = []
        with patch.object(Tenant, '_nc_admin_call', autospec=True,
                          side_effect=lambda rec, method, payload: calls.append(payload) or {'ok': True}):
            domain.with_user(self.padmin).action_set_primary()
            with self.assertRaises(UserError):
                domain.with_user(self.padmin).unlink()          # must be disabled first
            domain.with_user(self.padmin).action_disable()
        self.assertEqual(calls[-1]['host'], 'acme.platform.example')
        self.assertFalse(calls[-1]['redirect'])
        self.assertEqual(domain.state, 'disabled')
        self.assertNotIn('erp.customer.com', [r['fqdn'] for r in self.Domain._nc_edge_state()['domains']])
        domain.with_user(self.padmin).unlink()
        self.assertTrue(self.tenant.exists())
        self.assertEqual(self.tenant.database_name, 'acme')
        self.assertEqual(self.tenant.primary_address, 'acme.platform.example')

    def test_re_enabling_requires_a_fresh_proof(self):
        domain = self._activate()
        domain.with_user(self.padmin).action_disable()
        domain.with_user(self.padmin).action_enable()
        self.assertEqual(domain.state, 'pending_verification')
        self.assertTrue(domain.sudo().verification_token)

    def test_existing_subdomain_records_are_untouched_by_the_feature(self):
        subdomain = self.other.domain_ids
        self.assertEqual(subdomain.mapped('fqdn'), ['globex.platform.example'])
        self.assertEqual(subdomain.state, 'active')
        self.assertTrue(subdomain.is_primary)
