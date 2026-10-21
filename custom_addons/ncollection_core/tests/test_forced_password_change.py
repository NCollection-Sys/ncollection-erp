# -*- coding: utf-8 -*-
"""After an administrator resets a password the person must choose their own before anything else opens.

Drives the real HTTP stack: sign in with the temporary password, meet the wall on a web page and on the RPC routes,
get through to the change page, set a password, and find the flag lifted and the session ended. Synthetic users,
throwaway passwords.
"""
import json
import secrets

from odoo.tests import HttpCase, tagged

CHANGE_PATH = '/ncollection/password/change'


def _password():
    return 'Aa1!' + secrets.token_urlsafe(18)


@tagged('post_install', '-at_install')
class TestForcedPasswordChange(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        users = cls.env['res.users'].with_context(no_reset_password=True)
        group_user = cls.env.ref('base.group_user')
        cls.admin = users.create({
            'name': 'Reset Administrator', 'login': 'reset_admin',
            'group_ids': [(6, 0, [group_user.id, cls.env.ref('ncollection_core.group_role_owner').id])]})
        cls.person = users.create({
            'name': 'Reset Person', 'login': 'reset_person', 'group_ids': [(6, 0, [group_user.id])]})
        cls.free_password = _password()
        cls.free = users.create({
            'name': 'Nothing Pending', 'login': 'nothing_pending', 'password': cls.free_password,
            'group_ids': [(6, 0, [group_user.id])]})

    def _sign_in(self, login, password):
        """The real sign-in page (interactive), as a person does it: the HttpCase helper signs in as an RPC client."""
        page = self.url_open('/web/login')
        token = page.text.split('name="csrf_token" value="')[1].split('"')[0]
        response = self.url_open('/web/login', data={'csrf_token': token, 'login': login, 'password': password},
                                 allow_redirects=False)
        self.assertEqual(response.status_code, 303, 'the sign-in page accepted the password')

    def _reset_and_sign_in(self):
        self.temporary = self.person.with_user(self.admin).nc_reset_password()
        self.env.flush_all()
        self._sign_in('reset_person', self.temporary)

    def _rpc(self, model, method, args):
        response = self.url_open('/web/dataset/call_kw/%s/%s' % (model, method),
                                 headers={'Content-Type': 'application/json'},
                                 data=json.dumps({'jsonrpc': '2.0', 'method': 'call', 'params': {
                                     'model': model, 'method': method, 'args': args, 'kwargs': {}}}))
        return response.json()

    def test_a_web_page_is_replaced_by_the_change_page(self):
        self._reset_and_sign_in()
        response = self.url_open('/odoo', allow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertTrue(response.headers['Location'].endswith(CHANGE_PATH))
        page = self.url_open(CHANGE_PATH)
        self.assertEqual(page.status_code, 200)
        self.assertIn('Choose your own password', page.text)

    def test_the_rpc_routes_are_refused_until_the_password_is_changed(self):
        self._reset_and_sign_in()
        body = self._rpc('res.partner', 'search_count', [[]])
        self.assertIn('error', body)
        self.assertIn('choose a new password', json.dumps(body))

    def test_a_person_with_nothing_pending_is_not_held_back_and_has_no_use_for_the_page(self):
        self.authenticate('nothing_pending', self.free_password)
        self.assertEqual(self.url_open('/odoo', allow_redirects=False).status_code, 200)
        response = self.url_open(CHANGE_PATH, allow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertTrue(response.headers['Location'].endswith('/odoo'))

    def test_the_page_refuses_a_wrong_given_password_a_mismatch_and_an_unchanged_password(self):
        self._reset_and_sign_in()
        chosen = _password()
        token = self.url_open(CHANGE_PATH).text.split('name="csrf_token" value="')[1].split('"')[0]

        def post(old, new, confirm):
            return self.url_open(CHANGE_PATH, data={'csrf_token': token, 'old_password': old, 'password': new,
                                                    'confirm_password': confirm}, allow_redirects=False)

        self.assertIn('not correct', post('not-it', chosen, chosen).text)
        self.assertIn('not the same', post(self.temporary, chosen, chosen + 'x').text)
        self.assertIn('different', post(self.temporary, self.temporary, self.temporary).text)
        self.person.invalidate_recordset()
        self.assertTrue(self.person.nc_must_change_password, "a refused attempt changes nothing")

    def test_changing_the_password_lifts_the_flag_audits_it_and_ends_the_session(self):
        self._reset_and_sign_in()
        chosen = _password()
        token = self.url_open(CHANGE_PATH).text.split('name="csrf_token" value="')[1].split('"')[0]
        done = self.url_open(
            CHANGE_PATH, allow_redirects=False,
            data={'csrf_token': token, 'old_password': self.temporary, 'password': chosen, 'confirm_password': chosen})
        self.assertEqual(done.status_code, 303)
        self.person.invalidate_recordset()
        self.assertFalse(self.person.nc_must_change_password)
        row = self.env['ncollection.auth.log'].sudo().search([
            ('event_type', '=', 'password_changed'), ('user_id', '=', self.person.id)])
        self.assertTrue(row)
        self.assertNotIn(chosen, (row.detail or '') + (row.login or ''))
        # signed out: the old cookie no longer opens anything; the chosen password signs in, the temporary one does not
        self.assertEqual(self.url_open('/odoo', allow_redirects=False).status_code, 303)
        self._sign_in('reset_person', chosen)
        self.assertEqual(self.url_open('/odoo', allow_redirects=False).status_code, 200)
