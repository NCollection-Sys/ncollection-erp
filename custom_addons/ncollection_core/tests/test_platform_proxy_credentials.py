# -*- coding: utf-8 -*-
"""A platform administrator's entry session must not be able to leave a persistent credential behind.

Everything here runs in a REAL entry session: the grant is registered, the browser endpoint redeems it, and every
probe is a JSON-RPC call made with that session cookie (plus the ORM-level equivalents, ``with_user`` and ``sudo()``).
The proxy is an Owner + system administrator on purpose; what it must NOT be able to do is mint a secret that would
outlive its own 8-hour session: an API key (any path), or a password for itself or anyone else.
"""
import hashlib
import json
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import HttpCase, tagged

from ..models.platform_entry import PROXY_LOGIN_PREFIX, SESSION_MAX_SECONDS

NONCE = 'q' * 43
CANDIDATE = 'Zq7-candidate-value-4412'


def _post_json(case, url, params):
    return case.url_open(url, headers={'Content-Type': 'application/json'},
                         data='{"jsonrpc": "2.0", "method": "call", "params": %s}' % json.dumps(params))


@tagged('post_install', '-at_install')
class TestPlatformProxyCredentials(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.real = cls.env['res.users'].create({
            'name': 'Real Owner', 'login': 'real_owner_cred',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])]})

    def _enter(self):
        self.env['ncollection.platform.entry'].grant_from_platform({
            'token_hash': hashlib.sha256(NONCE.encode()).hexdigest(), 'platform_ref': 'u31',
            'platform_name': 'Cred Tester', 'return_url': 'https://platform.example/odoo', 'ttl': 120})
        self.env.cr.flush()
        response = self.url_open('/ncollection/platform/enter', data={'token': NONCE}, allow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.proxy = self.env['res.users'].sudo().with_context(active_test=False).search(
            [('login', '=', PROXY_LOGIN_PREFIX + 'u31')])
        self.assertTrue(self.proxy.nc_platform_proxy)

    def _rpc(self, model, method, args=None, kwargs=None):
        response = _post_json(self, '/web/dataset/call_kw/%s/%s' % (model, method), {
            'model': model, 'method': method, 'args': args or [], 'kwargs': kwargs or {}})
        return response.json()

    def _keys_of(self, user):
        self.env.cr.execute("SELECT count(*) FROM res_users_apikeys WHERE user_id = %s", [user.id])
        return self.env.cr.fetchone()[0]

    # ------------------------------------------------------------------ API keys
    def test_the_core_key_wizard_hands_the_proxy_no_key(self):
        self._enter()
        created = self._rpc('res.users.apikeys.description', 'create', [[{'name': 'persist'}]])
        self.assertNotIn('error', created, created)
        wizard_id = created['result'][0]
        made = self._rpc('res.users.apikeys.description', 'make_key', [[wizard_id]])
        self.assertNotIn('"key"', str(made.get('result')), "the wizard produced a key for a platform proxy")
        self.assertEqual(self._keys_of(self.proxy), 0)

    def test_no_orm_or_sudo_path_can_generate_a_key_for_the_proxy_or_anyone(self):
        self._enter()
        Keys = self.env['res.users.apikeys']
        with self.assertRaises(AccessError):
            Keys.with_user(self.proxy)._generate(None, 'persist', None)
        with self.assertRaises(AccessError):
            Keys.with_user(self.proxy).sudo()._generate(None, 'persist', None)       # sudo keeps the acting user
        self.assertEqual(self._keys_of(self.proxy), 0)
        self.assertEqual(self._keys_of(self.real), 0)
        # the private method is not an RPC endpoint at all
        self.assertIn('error', self._rpc('res.users.apikeys', '_generate', [None, 'x', None]))

    def test_a_named_tenant_user_can_still_create_a_key(self):
        """The guard is for the proxy only: a real, named user keeps the normal (identity-checked) right."""
        key = self.env['res.users.apikeys'].with_user(self.real).sudo()._generate(None, 'mine', None)
        self.assertTrue(key)

    # ------------------------------------------------------------------ passwords
    def test_the_proxy_cannot_set_a_password_for_itself_or_anyone(self):
        self._enter()
        Users = self.env['res.users'].with_user(self.proxy)
        for target in (self.proxy, self.real):
            with self.assertRaises(AccessError):
                Users.browse(target.id).write({'password': CANDIDATE})
            with self.assertRaises(AccessError):
                Users.browse(target.id).sudo().write({'password': CANDIDATE})
        with self.assertRaises(AccessError):
            Users.create({'name': 'Backdoor', 'login': 'backdoor_cred', 'password': CANDIDATE})
        # over RPC, with the real entry session
        result = self._rpc('res.users', 'write', [[self.real.id], {'password': CANDIDATE}])
        self.assertIn('error', result)
        self.env.cr.execute("SELECT password FROM res_users WHERE id IN %s", [(self.proxy.id, self.real.id)])
        self.assertEqual([row[0] for row in self.env.cr.fetchall()], [None, None])

    def test_the_proxy_has_no_password_path_even_if_a_password_were_stored(self):
        self._enter()
        self.env['res.users']._set_encrypted_password(
            self.proxy.id, self.env['res.users']._crypt_context().hash(CANDIDATE))
        self.env.cr.flush()
        response = self.url_open('/web/session/authenticate', headers={'Content-Type': 'application/json'}, data=(
            '{"jsonrpc": "2.0", "params": {"db": "%s", "login": "%s", "password": "%s"}}'
            % (self.env.cr.dbname, self.proxy.login, CANDIDATE)))
        self.assertIn('error', response.json())

    def test_a_real_owner_still_manages_passwords(self):
        """Nothing is taken from real tenant users: only the platform proxy is refused."""
        self.real.write({'password': CANDIDATE})
        self.env['res.users'].with_user(self.env.ref('base.user_admin')).browse(self.real.id).write(
            {'password': CANDIDATE + 'x'})

    # ------------------------------------------------------------------ the session itself
    def test_nothing_survives_the_session_window(self):
        self._enter()
        info = self.url_open('/web/session/get_session_info', headers={'Content-Type': 'application/json'},
                             data='{"jsonrpc": "2.0", "params": {}}').json()['result']
        self.assertEqual(info['username'], self.proxy.login)
        late = fields.Datetime.now() - timedelta(seconds=SESSION_MAX_SECONDS + 60)
        self.env.cr.execute("UPDATE ncollection_platform_entry SET used_at = %s WHERE user_id = %s",
                            [late, self.proxy.id])
        self.env.cr.flush()
        self.env.registry.clear_cache()
        after = self._rpc('res.users', 'read', [[self.real.id], ['name']])
        self.assertIn('error', after, "an expired platform session still answered an ORM call")
        # and the same cookie opens no backend page either
        page = self.url_open('/odoo', allow_redirects=False)
        self.assertNotEqual(page.status_code, 200)
