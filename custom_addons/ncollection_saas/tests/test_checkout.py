# -*- coding: utf-8 -*-
"""Public self-service checkout (P2-T16) — CI-safe tests.

Cover the funnel logic and abuse controls WITHOUT spawning odoo subprocesses or
creating real databases: the collision check is patched, and the
verify->provisioning path asserts the trigger fired (action_run) without
running the engine.
"""
import json
from unittest.mock import patch

from odoo.tests import HttpCase, TransactionCase, tagged

ENGINE = 'odoo.addons.ncollection_saas.models.provisioning_job.ProvisioningJob'
SUBSCR = 'odoo.addons.ncollection_saas.models.subscription.Subscription'


@tagged('post_install', '-at_install')
class TestCheckoutModel(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Tenant = cls.env['ncollection.tenant']
        cls.plan = cls.env['ncollection.subscription.plan'].create({
            'name': 'Starter', 'code': 'CHKSTARTER', 'monthly_price': 49, 'max_users': 5,
        })

    def test_subdomain_availability(self):
        with patch('%s._database_exists' % ENGINE, return_value=False):
            self.assertTrue(self.Tenant._nc_subdomain_availability('acme123')[0])
            # input is normalised (lower-cased) first, so 'Acme' -> 'acme' is valid
            self.assertTrue(self.Tenant._nc_subdomain_availability('Acme')[0])
            # invalid grammar (too short, digit-start, illegal chars)
            for bad in ('a', 'ab', '1abc', 'a-b', 'a b'):
                ok, reason = self.Tenant._nc_subdomain_availability(bad)
                self.assertFalse(ok)
                self.assertEqual(reason, 'invalid')
            # reserved / offensive
            for bad in ('admin', 'api', 'www', 'porn'):
                ok, reason = self.Tenant._nc_subdomain_availability(bad)
                self.assertFalse(ok)
                self.assertEqual(reason, 'reserved')
            # taken by an existing tenant
            self.Tenant.create({'company_name': 'Taken Co', 'database_name': 'takenco',
                                'plan_id': self.plan.id})
            ok, reason = self.Tenant._nc_subdomain_availability('takenco')
            self.assertFalse(ok)
            self.assertEqual(reason, 'taken')

    def test_availability_deep_flag_gates_physical_probe(self):
        # #226 M-1: the public availability endpoint (deep=False) must NOT open a
        # psycopg2 connection per call; the authoritative register path (deep=True)
        # still consults the physical DB probe.
        with patch('%s._database_exists' % ENGINE, return_value=False) as probe:
            ok, _ = self.Tenant._nc_subdomain_availability('freeone', deep=False)
            self.assertTrue(ok)
            probe.assert_not_called()
            ok2, _ = self.Tenant._nc_subdomain_availability('freeone', deep=True)
            self.assertTrue(ok2)
            probe.assert_called()

    def test_availability_shallow_still_flags_registry_taken(self):
        # a name taken in the tenant registry resolves without any DB probe,
        # even in the shallow (public) mode.
        self.Tenant.create({'company_name': 'Reg Co', 'database_name': 'regtaken',
                            'plan_id': self.plan.id})
        with patch('%s._database_exists' % ENGINE) as probe:
            ok, reason = self.Tenant._nc_subdomain_availability('regtaken', deep=False)
            self.assertFalse(ok)
            self.assertEqual(reason, 'taken')
            probe.assert_not_called()

    def test_recaptcha_skipped_when_unset(self):
        # no secret configured (default) => verification is skipped
        self.env['ir.config_parameter'].sudo().set_param('ncollection_saas.recaptcha_secret', '')
        self.assertTrue(self.Tenant._nc_verify_recaptcha('anything', '127.0.0.1'))

    def test_trial_quota_per_ip(self):
        cfg = self.env['ir.config_parameter'].sudo()
        cfg.set_param('ncollection_saas.trial_quota_per_ip', '1')
        self.Tenant.create({'company_name': 'Q1', 'database_name': 'qone',
                            'email': 'a@x.com', 'plan_id': self.plan.id,
                            'checkout_source_ip': '9.9.9.9'})
        self.assertTrue(self.Tenant._nc_trial_quota_exceeded('9.9.9.9', 'b@y.com'))
        self.assertFalse(self.Tenant._nc_trial_quota_exceeded('1.2.3.4', 'b@y.com'))

    def test_verify_triggers_provisioning_only_after_confirm(self):
        token = self.Tenant._nc_new_checkout_token()
        tenant = self.Tenant.create({
            'company_name': 'Verify Co', 'database_name': 'verifyco',
            'email': 'owner@verify.example', 'plan_id': self.plan.id,
            'status': 'trial', 'checkout_token': token,
        })
        sub = self.env['ncollection.subscription'].create({
            'tenant_id': tenant.id, 'plan_id': self.plan.id, 'status': 'draft',
        })
        tenant.subscription_id = sub.id
        # before verify: not verified, subscription still draft
        self.assertFalse(tenant.checkout_email_verified)
        self.assertEqual(sub.status, 'draft')
        # confirm -> subscription activates (which triggers provisioning). Patch
        # the trigger so nothing spawns; assert it was invoked.
        with patch('%s._trigger_provisioning' % SUBSCR) as trig:
            confirmed = self.Tenant._nc_confirm_checkout(token)
        self.assertEqual(confirmed, tenant)
        self.assertTrue(tenant.checkout_email_verified)
        self.assertEqual(sub.status, 'active')
        trig.assert_called()

    def test_confirm_bad_token_is_noop(self):
        self.assertFalse(self.Tenant._nc_confirm_checkout('nope'))


@tagged('post_install', '-at_install')
class TestCheckoutHttp(HttpCase):

    def setUp(self):
        super().setUp()
        self.plan = self.env['ncollection.subscription.plan'].create({
            'name': 'Starter', 'code': 'CHKSTARTER', 'monthly_price': 49,
            'yearly_price': 490, 'max_users': 5, 'description': 'For small teams',
        })

    def _rpc(self, url, params):
        res = self.url_open(url, data=json.dumps(
            {'jsonrpc': '2.0', 'method': 'call', 'params': params}),
            headers={'Content-Type': 'application/json'})
        self.assertEqual(res.status_code, 200)
        return res.json()['result']

    def test_pricing_page_loads_branded(self):
        res = self.url_open('/pricing')
        self.assertEqual(res.status_code, 200)
        self.assertIn('Starter', res.text)
        self.assertNotIn('>Odoo<', res.text)

    def test_register_creates_unverified_tenant_without_provisioning(self):
        result = self._rpc('/nc/checkout/register', {
            'company': 'Stranger LLC', 'contact': 'Sam', 'email': 'sam@stranger.example',
            'subdomain': 'strangerllc', 'plan': 'CHKSTARTER', 'cycle': 'monthly',
        })
        self.assertTrue(result.get('success'), result)
        tenant = self.env['ncollection.tenant'].sudo().search(
            [('database_name', '=', 'strangerllc')], limit=1)
        self.assertTrue(tenant)
        # provisioning must NOT have fired before email verification (§11)
        self.assertFalse(tenant.checkout_email_verified)
        self.assertEqual(tenant.database_status, 'not_provisioned')
        sub = tenant.subscription_id
        self.assertTrue(sub and sub.status == 'draft')

    def test_register_rejects_reserved_subdomain(self):
        result = self._rpc('/nc/checkout/register', {
            'company': 'X', 'email': 'x@x.example', 'subdomain': 'admin', 'plan': 'CHKSTARTER',
        })
        self.assertFalse(result.get('success'))
        self.assertEqual(result.get('error'), 'subdomain_reserved')

    def test_register_rejects_overlong_free_text(self):
        # #226 M-3: a defensive length cap on the unbounded free-text fields.
        result = self._rpc('/nc/checkout/register', {
            'company': 'X' * 201, 'email': 'x@x.example', 'subdomain': 'longco',
            'plan': 'CHKSTARTER',
        })
        self.assertFalse(result.get('success'))
        self.assertEqual(result.get('error'), 'field_too_long')

    def test_register_accepts_exactly_max_length(self):
        # the cap is `> 200`, so exactly 200 chars must be ACCEPTED (boundary).
        result = self._rpc('/nc/checkout/register', {
            'company': 'C' * 200, 'contact': 'N' * 200, 'email': 'ok@x.example',
            'subdomain': 'boundaryco', 'plan': 'CHKSTARTER',
        })
        self.assertNotEqual(result.get('error'), 'field_too_long', result)

    def test_status_login_url_uses_base_domain_not_localhost(self):
        """#210: a ready tenant's login_url must be built from the canonical
        ncollection_saas.base_domain param (seeded 'ncollectionerp.com'), NOT the
        orphan 'tenant_base_domain' key that nothing seeds and so fell back to
        'localhost' in every environment including production."""
        tenant = self.env['ncollection.tenant'].sudo().create({
            'company_name': 'Ready Co', 'database_name': 'readyco',
            'plan_id': self.plan.id, 'database_status': 'ready',
        })
        res = self._rpc('/nc/checkout/status', {'tenant_uuid': tenant.tenant_uuid})
        self.assertEqual(res['status'], 'ready')
        self.assertEqual(res['login_url'], 'https://readyco.ncollectionerp.com/odoo')
        self.assertNotIn('localhost', res['login_url'])
        # an explicit deployment override is still honoured
        self.env['ir.config_parameter'].sudo().set_param(
            'ncollection_saas.base_domain', 'tenants.example.com')
        res2 = self._rpc('/nc/checkout/status', {'tenant_uuid': tenant.tenant_uuid})
        self.assertEqual(res2['login_url'], 'https://readyco.tenants.example.com/odoo')
