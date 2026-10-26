# -*- coding: utf-8 -*-
"""Platform administrator entry, TENANT side (ncollection.platform.entry, proxy user, credential, endpoints)."""
import hashlib
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessDenied, AccessError, UserError
from odoo.tests import HttpCase, TransactionCase, tagged

from ..models.platform_entry import CREDENTIAL_TYPE, PROXY_LOGIN_PREFIX

NONCE = 'T0ken-for-tests-' + 'x' * 30


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _grant(token=NONCE, ref='u7', name='Alice Admin', **extra):
    return dict({'token_hash': _hash(token), 'platform_ref': ref, 'platform_name': name,
                 'return_url': 'https://platform.example/odoo', 'ttl': 60}, **extra)


@tagged('post_install', '-at_install')
class TestPlatformEntryModel(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Entry = cls.env['ncollection.platform.entry']
        cls.sync_user = cls.env['res.users'].create({
            'name': 'Sync service', 'login': 'nc_sync_svc@ncollection.internal',
            'group_ids': [(6, 0, [cls.env.ref('ncollection_core.group_config_sync').id])]})

    # ------------------------------------------------------------------ the platform's grant
    def test_a_valid_grant_registers_only_the_hash_with_a_short_life(self):
        result = self.Entry.grant_from_platform(_grant())
        entry = self.Entry.browse(result['id'])
        self.assertEqual(entry.token_hash, _hash(NONCE))
        self.assertNotIn(NONCE, str(entry.read()))
        self.assertEqual(entry.state, 'granted')
        self.assertLessEqual(entry.expires_at, fields.Datetime.now() + timedelta(seconds=61))

    def test_the_lifetime_is_clamped(self):
        short = self.Entry.browse(self.Entry.grant_from_platform(_grant('a' * 40, ttl=1))['id'])
        long = self.Entry.browse(self.Entry.grant_from_platform(_grant('b' * 40, ttl=10 ** 6))['id'])
        self.assertGreaterEqual(short.expires_at, fields.Datetime.now() + timedelta(seconds=8))
        self.assertLessEqual(long.expires_at, fields.Datetime.now() + timedelta(seconds=121))

    def test_malformed_grants_are_refused(self):
        bad = [
            {**_grant(), 'groups': [1]},                         # a key the platform may not send
            {**_grant(), 'password': 'x'},
            _grant(ref='U7'), _grant(ref='a' * 25), _grant(ref='u 7'), _grant(ref=''),
            {**_grant(), 'token_hash': 'abc'},
            {**_grant(), 'token_hash': 'Z' * 64},
            _grant(name='   '),
            {**_grant(), 'return_url': 'javascript:alert(1)'},
            {**_grant(), 'return_url': 'https://'},
        ]
        for vals in bad:
            with self.assertRaises(UserError, msg=str(vals)):
                self.Entry.grant_from_platform(vals)
        with self.assertRaises(UserError):
            self.Entry.grant_from_platform('not a dict')

    def test_the_same_token_cannot_be_registered_twice(self):
        self.Entry.grant_from_platform(_grant())
        with self.assertRaises(Exception):
            with self.cr.savepoint():
                self.Entry.grant_from_platform(_grant())

    def test_the_service_account_cannot_skip_the_checks_by_calling_create_directly(self):
        """json2 exposes the public ``create``: it must run the same validation as ``grant_from_platform``."""
        Entry = self.Entry.with_user(self.sync_user)
        far = '2099-01-01 00:00:00'
        for forged in ({'expires_at': far}, {'used_at': far}, {'user_id': 1}, {'platform_ref': 'admin'},
                       {'return_url': 'javascript:alert(1)'}, {'token_hash': 'abc'}):
            with self.assertRaises(UserError, msg=str(forged)):
                Entry.create({'platform_name': 'X', **_grant('j' * 40), **forged})
        entry = Entry.create({'token_hash': _hash('k' * 40), 'platform_ref': 'u9', 'platform_name': 'Z'})
        entry = entry.sudo()
        self.assertFalse(entry.used_at)
        self.assertLessEqual(entry.expires_at, fields.Datetime.add(fields.Datetime.now(), seconds=121))

    def test_only_the_config_sync_service_may_create_grants(self):
        self.assertTrue(self.Entry.with_user(self.sync_user).grant_from_platform(_grant())['ok'])
        with self.assertRaises(AccessError):
            self.Entry.with_user(self.sync_user).search([])                                  # create only: no read
        plain = self.env['res.users'].create({
            'name': 'Plain', 'login': 'plain_user', 'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        with self.assertRaises(AccessError):
            self.Entry.with_user(plain).grant_from_platform(_grant('c' * 40))

    def test_a_tenant_admin_can_read_the_audit_trail_but_not_edit_it(self):
        entry = self.Entry.browse(self.Entry.grant_from_platform(_grant())['id'])
        admin = self.env['res.users'].create({
            'name': 'Tenant admin', 'login': 'tenant_adm',
            'group_ids': [(6, 0, [self.env.ref('base.group_system').id])]})
        self.assertEqual(entry.with_user(admin).platform_name, 'Alice Admin')
        with self.assertRaises(AccessError):
            entry.with_user(admin).write({'platform_name': 'Mallory'})
        with self.assertRaises(AccessError):
            entry.with_user(admin).unlink()

    # ------------------------------------------------------------------ the proxy user
    def _proxy(self, ref='u7', name='Alice Admin', token=NONCE):
        entry = self.Entry.browse(self.Entry.grant_from_platform(_grant(token, ref, name))['id'])
        return entry, self.Entry._proxy_user(entry)

    def test_the_proxy_is_named_passwordless_and_never_a_customer_seat(self):
        _entry, proxy = self._proxy()
        self.assertEqual(proxy.login, PROXY_LOGIN_PREFIX + 'u7')
        self.assertEqual(proxy.name, 'Platform Admin: Alice Admin')
        self.assertTrue(proxy.nc_platform_proxy)
        self.env.cr.execute('SELECT password FROM res_users WHERE id = %s', [proxy.id])
        self.assertFalse(self.env.cr.fetchone()[0])
        # The seat count excludes the proxy: with the limit set to exactly the real users, it still passes.
        real = self.env['res.users'].sudo().search_count([
            ('active', '=', True), ('share', '=', False), ('nc_platform_proxy', '=', False)])
        Config = type(self.env['ncollection.workspace.config'])
        with patch.object(Config, 'get_max_users', return_value=real):
            self.env['res.users']._ncollection_check_user_limit()

    def test_a_proxy_is_reused_per_platform_admin_and_a_squatter_is_refused(self):
        _e1, first = self._proxy(token='d' * 40)
        _e2, again = self._proxy(token='e' * 40)
        self.assertEqual(first, again)
        _e3, other = self._proxy(ref='u8', name='Bob', token='f' * 40)
        self.assertNotEqual(first, other)
        squatter = self.env['res.users'].create({
            'name': 'Squatter', 'login': PROXY_LOGIN_PREFIX + 'u9',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        self.assertFalse(squatter.nc_platform_proxy)
        entry = self.Entry.browse(self.Entry.grant_from_platform(_grant('g' * 40, 'u9'))['id'])
        with self.assertRaises(AccessDenied):
            self.Entry._proxy_user(entry)

    # ------------------------------------------------------------------ the credential
    def _login(self, user, token, kind=CREDENTIAL_TYPE):
        credential = {'type': kind, 'login': user.login, 'token': token, 'password': token}
        return user.with_user(user)._check_credentials(credential, {'interactive': True, 'REMOTE_ADDR': '10.0.0.9'})

    def test_the_token_signs_the_proxy_in_exactly_once(self):
        entry, proxy = self._proxy()
        info = self._login(proxy, NONCE)
        self.assertEqual(info['uid'], proxy.id)
        self.assertEqual(info['mfa'], 'default')               # a workspace's second-factor policy still applies
        entry.invalidate_recordset()
        self.assertEqual(entry.state, 'used')
        self.assertEqual(entry.user_id, proxy)
        self.assertEqual(entry.remote_ip, '10.0.0.9')
        with self.assertRaises(AccessDenied):
            self._login(proxy, NONCE)                          # replay

    def test_an_expired_or_wrong_or_foreign_token_signs_nobody_in(self):
        entry, proxy = self._proxy()
        with self.assertRaises(AccessDenied):
            self._login(proxy, 'wrong-token-' + 'y' * 30)
        entry.sudo().write({'expires_at': fields.Datetime.now() - timedelta(hours=1)})
        with self.assertRaises(AccessDenied):
            self._login(proxy, NONCE)
        _e2, other = self._proxy(ref='u8', name='Bob', token='h' * 40)
        with self.assertRaises(AccessDenied):
            self._login(other, 'x' * 40)
        # a token granted to u7 cannot be redeemed by u8's proxy
        self.Entry.grant_from_platform(_grant('i' * 40, 'u7'))
        with self.assertRaises(AccessDenied):
            self._login(other, 'i' * 40)

    def test_only_a_proxy_may_present_the_entry_credential_and_a_proxy_has_no_password_path(self):
        self.Entry.grant_from_platform(_grant('j' * 40, 'u1'))
        real = self.env['res.users'].create({
            'name': 'Real', 'login': 'real_person',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        with self.assertRaises(AccessDenied):
            self._login(real, 'j' * 40)
        _entry, proxy = self._proxy()
        with self.assertRaises(AccessDenied):
            self._login(proxy, 'anything', kind='password')

    def test_the_session_ends_by_itself_after_the_window(self):
        entry, proxy = self._proxy()
        self._login(proxy, NONCE)
        self.assertFalse(proxy._compute_session_token('sid').startswith('expired:'))
        entry.sudo().write({'used_at': fields.Datetime.now() - timedelta(hours=9)})
        self.assertTrue(proxy._compute_session_token('sid').startswith('expired:'))
        real = self.env.ref('base.user_admin')
        self.assertFalse(real._compute_session_token('sid').startswith('expired:'))     # nobody else is affected

    def test_a_tenant_admin_cannot_forge_or_mark_proxy_accounts(self):
        admin = self.env['res.users'].create({
            'name': 'Tenant admin 2', 'login': 'tenant_adm2',
            'group_ids': [(6, 0, [self.env.ref('base.group_system').id])]})
        real = self.env['res.users'].create({
            'name': 'Real', 'login': 'real_for_flag',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        with self.assertRaises(AccessError):
            real.with_user(admin).write({'nc_platform_proxy': True})          # would leave the seat count
        with self.assertRaises(AccessError):
            self.env['res.users'].with_user(admin).create({
                'name': 'Forged', 'login': 'platform.u99', 'nc_platform_proxy': True, 'nc_platform_ref': 'u99'})
        self.assertFalse(real.nc_platform_proxy)

    def test_the_banner_name_is_the_platform_admins_own_name(self):
        _entry, proxy = self._proxy(name='Alice Admin')
        self._login(proxy, NONCE)
        self.assertEqual(self.Entry._session_admin_name(proxy), 'Alice Admin')

    # ------------------------------------------------------------------ attribution
    def test_what_the_platform_admin_does_is_written_by_their_named_proxy(self):
        _entry, proxy = self._proxy()
        partner = self.env['res.partner'].with_user(proxy).create({'name': 'Created inside the tenant'})
        self.assertEqual(partner.create_uid, proxy)
        self.assertEqual(partner.create_uid.name, 'Platform Admin: Alice Admin')
        self.assertNotEqual(partner.create_uid, self.env.ref('base.user_admin'))


@tagged('post_install', '-at_install')
class TestPlatformEntryEndpoints(HttpCase):

    def _grant(self, token, ref='u7'):
        return self.env['ncollection.platform.entry'].grant_from_platform(_grant(token, ref))

    def test_enter_signs_in_once_and_lands_on_the_summary(self):
        self._grant(NONCE)
        response = self.url_open('/ncollection/platform/enter', data={'token': NONCE}, allow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertTrue(response.headers['Location'].endswith('/atlas'), 'into the application path, never /odoo')
        info = self.url_open('/web/session/get_session_info', headers={'Content-Type': 'application/json'},
                             data='{"jsonrpc": "2.0", "params": {}}').json()['result']
        self.assertEqual(info['username'], PROXY_LOGIN_PREFIX + 'u7')
        replay = self.url_open('/ncollection/platform/enter', data={'token': NONCE}, allow_redirects=False)
        self.assertEqual(replay.status_code, 303, "the same signed-in browser repeating the POST just goes on")
        self.env.cr.execute("UPDATE ncollection_platform_entry SET used_at = used_at - interval '1 minute'")
        self.env.invalidate_all()
        late = self.url_open('/ncollection/platform/enter', data={'token': NONCE}, allow_redirects=False)
        self.assertEqual(late.status_code, 403, "a used link is refused once the moment has passed")

    def test_a_used_link_is_refused_to_anyone_not_signed_in_as_that_proxy(self):
        self._grant(NONCE)
        self.url_open('/ncollection/platform/enter', data={'token': NONCE}, allow_redirects=False)
        self.authenticate(None, None)                              # another browser: no session
        replay = self.url_open('/ncollection/platform/enter', data={'token': NONCE}, allow_redirects=False)
        self.assertEqual(replay.status_code, 403)

    def test_a_browser_gets_a_page_that_says_what_to_do_and_everyone_else_the_bare_word(self):
        page = self.url_open('/ncollection/platform/enter', data={'token': 'nope'}, allow_redirects=False,
                             headers={'Accept': 'text/html,application/xhtml+xml'})
        self.assertEqual(page.status_code, 403)
        self.assertIn('text/html', page.headers['Content-Type'])
        self.assertIn('Enter Company', page.text)
        self.assertNotIn('nope', page.text)
        bare = self.url_open('/ncollection/platform/enter', data={'token': 'nope'}, allow_redirects=False)
        self.assertEqual(bare.text, 'Forbidden')

    def test_every_refusal_is_the_same_bare_403(self):
        for token in (None, '', 'nope', 'z' * 500):
            body = {'token': token} if token is not None else {'other': '1'}      # an empty body would be a GET
            response = self.url_open('/ncollection/platform/enter', data=body, allow_redirects=False)
            self.assertEqual(response.status_code, 403, token)
            self.assertEqual(response.text, 'Forbidden', token)

    def test_the_token_is_never_accepted_in_the_query_string(self):
        """A GET carrying ?token= is just the bridge page: it neither looks the token up nor signs anyone in, and
        the page itself reads the token from the URL fragment only."""
        self._grant(NONCE)
        response = self.url_open('/ncollection/platform/enter?token=' + NONCE, allow_redirects=False)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(NONCE, response.text)
        self.assertEqual(response.headers['Referrer-Policy'], 'no-referrer')
        self.assertIn('location.hash', response.text)
        self.assertNotIn('location.search', response.text)
        entry = self.env['ncollection.platform.entry'].search([])
        self.assertFalse(entry.used_at)                        # nothing was redeemed

    def test_a_bridge_without_a_fragment_goes_on_instead_of_showing_a_bare_forbidden(self):
        """A reload or a replayed link has no fragment: the page sends an anonymous browser to the login and a
        signed-in one into the app. It grants nothing, so isolation is unchanged."""
        anonymous = self.url_open('/ncollection/platform/enter', allow_redirects=False)
        self.assertEqual(anonymous.status_code, 200)
        self.assertIn('location.replace("/web/login")', anonymous.text)
        self.assertNotIn('"Forbidden"', anonymous.text)
        self.authenticate('admin', 'admin')
        signed_in = self.url_open('/ncollection/platform/enter', allow_redirects=False)
        self.assertIn('location.replace("/atlas")', signed_in.text)

    def test_exit_ends_the_session_and_returns_to_the_platform(self):
        self._grant(NONCE)
        self.url_open('/ncollection/platform/enter', data={'token': NONCE}, allow_redirects=False)
        response = self.url_open('/ncollection/platform/exit', allow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers['Location'], 'https://platform.example/odoo')
        after = self.url_open('/ncollection/platform/exit', allow_redirects=False)
        self.assertNotEqual(after.status_code, 200)            # no session any more

    def test_a_normal_user_cannot_use_the_exit_route(self):
        self.authenticate('admin', 'admin')
        response = self.url_open('/ncollection/platform/exit', allow_redirects=False)
        self.assertEqual(response.status_code, 403)
