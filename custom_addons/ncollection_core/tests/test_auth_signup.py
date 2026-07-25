# -*- coding: utf-8 -*-
"""Public signup gate (P3-T12 / finding C-1) — CI-safe tests.

The /ncollection/api/signup route ships in every tenant (ncollection_core is in
CORE_TENANT_MODULES). Left open it lets an unauthenticated caller create an
Internal User in any tenant's live ERP. It must therefore be DISABLED by default
and only run where `ncollection_core.public_signup_enabled` is explicitly on.
"""
import json

from odoo.tests import HttpCase, tagged

FLAG = 'ncollection_core.public_signup_enabled'


@tagged('post_install', '-at_install')
class TestPublicSignupGate(HttpCase):

    def setUp(self):
        super().setUp()
        # This test flips a SECURITY-critical flag. Guarantee it returns to the
        # secure default even if TransactionCase rollback doesn't hold in a given
        # invocation (e.g. an ad-hoc `-u ... --stop-after-init` against a live DB),
        # so running it can never leave public signup re-enabled.
        self.addCleanup(
            self.env['ir.config_parameter'].sudo().set_param, FLAG, 'False')

    def _signup(self, **params):
        return self.url_open(
            '/ncollection/api/signup',
            data=json.dumps({'jsonrpc': '2.0', 'method': 'call', 'params': params}),
            headers={'Content-Type': 'application/json'},
        ).json().get('result', {})

    def test_signup_blocked_by_default(self):
        self.env['ir.config_parameter'].sudo().set_param(FLAG, 'False')
        # password is a placeholder — never checked (the gate blocks first).
        result = self._signup(name='Mallory', email='m@evil.com', password='placeholder')
        self.assertEqual(result.get('error'), 'signup_disabled',
                         "public signup must be OFF by default")

    def test_signup_runs_when_flag_enabled(self):
        # Enabled → the gate opens and normal validation runs. Empty fields keep
        # the test from creating a real user while still proving the gate passed.
        self.env['ir.config_parameter'].sudo().set_param(FLAG, 'True')
        result = self._signup(name='', email='', password='')
        self.assertEqual(result.get('error'), 'missing_fields',
                         "flag on -> request is processed (validation ran)")
