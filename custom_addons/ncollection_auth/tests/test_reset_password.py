# -*- coding: utf-8 -*-
"""The forgot-password flow on a tenant login: reachable, the same answer for every login, honest when mail cannot be
sent, and a token that is single-use, expiring, tenant-bound and mailed on the tenant's own URL."""

import logging
import re
from unittest.mock import patch

from odoo.addons.mail.models.mail_mail import MailMail
from odoo.exceptions import AccessDenied
from odoo.tests import HttpCase, tagged
from odoo.tools import config

OLD, NEW = 'OldPass-Alpha-91!', 'NewPass-Bravo-27!'
SENT = 'If an account matches'


@tagged('post_install', '-at_install')
class TestResetPassword(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['ir.config_parameter'].sudo().set_param('web.base.url', 'https://acme.tenant.example')
        cls.env['ir.config_parameter'].sudo().set_param('auth_signup.reset_password', 'True')
        cls.user = cls.env['res.users'].create({
            'name': 'Reset Subject', 'login': 'resetuser', 'email': 'reset.subject@example.com',
            'password': OLD, 'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])]})
        cls.env.flush_all()

    def _mail_server(self):
        self.outbox = []
        self.patch(MailMail, '_send', lambda this, *a, **k: self.outbox.extend(this.mapped('body_html')) or None)
        self.env['ir.mail_server'].search([]).write({'active': False})
        self.env['ir.mail_server'].create({'name': 'Test SMTP', 'smtp_host': 'localhost', 'smtp_port': 2525})

    def _csrf(self, url):
        return re.search(r'name="csrf_token" value="([^"]+)"', self.url_open(url).text).group(1)

    def _request(self, login):
        self.authenticate(None, None)
        return self.url_open('/web/reset_password', data={
            'login': login, 'csrf_token': self._csrf('/web/reset_password')})

    def _set(self, token, password):
        self.authenticate(None, None)
        url = '/web/reset_password?token=' + token
        return self.url_open(url, data={'password': password, 'confirm_password': password, 'token': token,
                                        'csrf_token': self._csrf(url)})

    def _outbox_body(self):
        """Text of the last email handed to the (patched) SMTP layer, decoded."""
        return self.outbox[-1]

    def _token(self):
        """The token the emailed link carries (the mail body is the only place it exists)."""
        found = re.search(r"token=([A-Za-z0-9_.~%-]+)", self._outbox_body())
        return found.group(1) if found else None

    def _can_log_in(self, password):
        try:
            self.user.with_user(self.user)._check_credentials(
                {'type': 'password', 'password': password}, {'interactive': True})
            return True
        except AccessDenied:
            return False

    def test_the_login_page_offers_the_flow(self):
        self.authenticate(None, None)
        self.assertIn('/web/reset_password', self.url_open('/web/login').text)

    def test_known_and_unknown_logins_get_the_same_answer(self):
        self._mail_server()
        known = self._request('resetuser').text
        unknown = self._request('no-such-login').text
        self.assertIn(SENT, known)
        self.assertIn(SENT, unknown)
        self.assertNotIn('No account found', unknown)
        self.assertEqual(len(re.findall(SENT, known)), len(re.findall(SENT, unknown)))

    def test_a_login_without_an_email_is_not_disclosed_either(self):
        self._mail_server()
        self.user.partner_id.email = False
        self.assertIn(SENT, self._request('resetuser').text)

    def test_without_a_mail_server_the_page_says_so_for_every_login(self):
        self.env['ir.mail_server'].search([]).write({'active': False})

        with patch.dict(config.options, {'smtp_server': 'localhost'}):     # Odoo's own default: nothing listens there
            for login in ('resetuser', 'no-such-login'):
                text = self._request(login).text
                self.assertIn('not set up for this workspace', text, login)
                self.assertNotIn(SENT, text)

    def test_the_emailed_link_is_on_the_tenants_own_url_and_the_token_never_reaches_the_log(self):
        self._mail_server()
        records = []
        handler = logging.Handler(level=logging.DEBUG)
        handler.emit = lambda record: records.append(record.getMessage())
        root = logging.getLogger()
        root.addHandler(handler)
        self.addCleanup(root.removeHandler, handler)
        self._request("resetuser")
        token = self._token()
        self.assertTrue(token)
        self.assertFalse([line for line in records if token in line], "the token must never be logged")
        body = self._outbox_body()
        self.assertIn("https://acme.tenant.example/web/reset_password", body)
        self.assertIn(token, body)

    def test_a_valid_token_sets_the_new_password_and_the_old_one_stops_working(self):
        self._mail_server()
        self._request('resetuser')
        token = self._token()
        self.assertIn('reset successfully', self._set(token, NEW).text)
        self.env.flush_all()
        self.assertTrue(self._can_log_in(NEW))
        self.assertFalse(self._can_log_in(OLD))

    def test_a_token_works_once(self):
        self._mail_server()
        self._request('resetuser')
        token = self._token()
        self._set(token, NEW)
        again = self._set(token, 'Another-Pass-55!')
        self.assertNotIn('reset successfully', again.text)
        self.env.flush_all()
        self.assertTrue(self._can_log_in(NEW), "the second use must not change the password")

    def test_an_expired_token_is_refused(self):
        token = self.user.partner_id._generate_signup_token(expiration=-1)
        self.assertNotIn('reset successfully', self._set(token, NEW).text)
        self.env.flush_all()
        self.assertTrue(self._can_log_in(OLD))

    def test_a_token_from_another_tenant_is_refused(self):
        """Tokens live on this database's partner rows only; one minted elsewhere matches nothing here."""
        foreign = 'Zx9' * 14
        self.assertNotIn('reset successfully', self._set(foreign, NEW).text)
        self.env.flush_all()
        self.assertTrue(self._can_log_in(OLD))

    def test_the_sign_in_page_follows_the_language(self):
        """?lang= (the switch on the page) and the browser's language pick an INSTALLED language; nothing else."""
        self.env['res.lang']._activate_lang('ar_001')
        self.env['ir.module.module']._load_module_terms(['ncollection_branding'], ['ar_001'], overwrite=True)
        self.env.flush_all()
        self.authenticate(None, None)
        arabic, english = 'نسيت كلمة المرور؟', 'Forgot password?'
        self.assertIn(arabic, self.url_open('/web/login?lang=ar_001').text)
        self.assertIn(english, self.url_open('/web/login').text)
        self.assertIn(arabic, self.url_open('/web/login', headers={'Accept-Language': 'ar-AE'}).text)
        self.assertIn(english, self.url_open('/web/login?lang=zz_ZZ').text, "a language not installed is ignored")
