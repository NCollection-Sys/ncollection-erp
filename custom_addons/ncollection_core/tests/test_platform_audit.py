# -*- coding: utf-8 -*-
"""Audit separation: what a Platform Administrator does inside a tenant is attributable to THEIR named identity,
what a tenant user does stays attributable to that user, and nothing is written to look like the other.

The proxy user is a real account (``platform.<ref>``, shown as "Platform Admin: <person>") that signs in by a
one-time grant. These tests go through the real entry endpoint and the real JSON-RPC session, then read what the ORM
recorded: create_uid / write_uid, the chatter author and the authentication log. No audit field is ever set by hand.
"""
import json
import secrets
from unittest.mock import patch

from odoo.tests import HttpCase, tagged
from odoo.tools import mute_logger

from .test_platform_entry import NONCE, _grant

PASSWORD = 'Aa1!' + secrets.token_urlsafe(18)       # a throwaway per run, never a literal in the repo


@tagged('post_install', '-at_install')
class TestPlatformAuditAttribution(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tenant_user = cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Tenant Accountant', 'login': 'tenant_acct', 'password': PASSWORD,
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id, cls.env.ref('base.group_partner_manager').id])]})
        cls.env.flush_all()

    def _rpc(self, model, method, args, kwargs=None):
        response = self.url_open(
            '/web/dataset/call_kw/%s/%s' % (model, method), headers={'Content-Type': 'application/json'},
            data=json.dumps({'jsonrpc': '2.0', 'method': 'call', 'params': {
                'model': model, 'method': method, 'args': args, 'kwargs': kwargs or {}}}))
        body = response.json()
        self.assertNotIn('error', body, body)
        return body['result']

    def _enter(self, token=NONCE, ref='u7', name='Alice Admin'):
        self.env['ncollection.platform.entry'].grant_from_platform(_grant(token, ref, name))
        response = self.url_open('/ncollection/platform/enter', data={'token': token}, allow_redirects=False)
        self.assertEqual(response.status_code, 303)
        return self.env['res.users'].with_context(active_test=False).search([('login', '=', 'platform.' + ref)])

    # ------------------------------------------------------------------ attribution
    def test_a_tenant_users_work_is_attributed_to_that_user(self):
        self.authenticate('tenant_acct', PASSWORD)
        partner_id = self._rpc('res.partner', 'create', [{'name': 'Made by the tenant user'}])
        self._rpc('res.partner', 'write', [[partner_id], {'comment': 'edited by the tenant user'}])
        partner = self.env['res.partner'].browse(partner_id)
        self.assertEqual((partner.create_uid, partner.write_uid), (self.tenant_user, self.tenant_user))
        self._rpc('res.partner', 'message_post', [[partner_id]], {'body': 'a note'})
        message = partner.message_ids[:1]
        self.assertEqual(message.author_id, self.tenant_user.partner_id)

    def test_what_the_platform_admin_does_is_attributed_to_the_platform_admin(self):
        proxy = self._enter()
        self.assertTrue(proxy.nc_platform_proxy)
        self.assertEqual(proxy.name, 'Platform Admin: Alice Admin')
        partner_id = self._rpc('res.partner', 'create', [{'name': 'Made by the platform admin'}])
        self._rpc('res.partner', 'write', [[partner_id], {'comment': 'edited by the platform admin'}])
        self._rpc('res.partner', 'message_post', [[partner_id]], {'body': 'a note from the platform admin'})
        partner = self.env['res.partner'].browse(partner_id)
        self.assertEqual((partner.create_uid, partner.write_uid), (proxy, proxy))
        self.assertEqual(partner.message_ids[:1].author_id, proxy.partner_id)
        self.assertNotIn(partner.create_uid, self.tenant_user | self.env.ref('base.user_admin'))

    def test_the_two_trails_stay_separate_when_both_work_on_the_same_record(self):
        self.authenticate('tenant_acct', PASSWORD)
        partner_id = self._rpc('res.partner', 'create', [{'name': 'Shared record'}])
        proxy = self._enter()
        self._rpc('res.partner', 'write', [[partner_id], {'comment': 'platform admin was here'}])
        partner = self.env['res.partner'].browse(partner_id)
        self.assertEqual(partner.create_uid, self.tenant_user, "the author of the record is not rewritten")
        self.assertEqual(partner.write_uid, proxy, "the last writer is the platform admin, truthfully")

    def test_two_platform_admins_are_two_identities(self):
        first = self._enter(NONCE, 'u7', 'Alice Admin')
        self.url_open('/ncollection/platform/exit', allow_redirects=False)
        second = self._enter('Another-' + NONCE, 'u8', 'Bob Admin')
        self.assertNotEqual(first, second)
        self.assertEqual((first.name, second.name), ('Platform Admin: Alice Admin', 'Platform Admin: Bob Admin'))

    # ------------------------------------------------------------------ the authentication log
    def test_entry_and_exit_are_in_the_authentication_log_under_the_proxy(self):
        if 'ncollection.auth.log' not in self.env:
            return          # a tenant without the auth module has no such log (the attribution above still holds)
        Log = self.env['ncollection.auth.log'].sudo()
        proxy = self._enter()
        entered = Log.search([('event_type', '=', 'platform_entry')])
        self.assertEqual(entered.user_id, proxy)
        self.assertEqual(entered.login, proxy.login)
        self.url_open('/ncollection/platform/exit', allow_redirects=False)
        left = Log.search([('event_type', '=', 'platform_exit')])
        self.assertEqual(left.user_id, proxy)

    def test_an_ordinary_login_is_still_a_login_not_a_platform_entry(self):
        if 'ncollection.auth.log' not in self.env:
            return
        self.authenticate('tenant_acct', PASSWORD)
        Log = self.env['ncollection.auth.log'].sudo()
        self.assertFalse(Log.search([('event_type', 'in', ('platform_entry', 'platform_exit'))]))

    # ------------------------------------------------------------------ privileges stay where they were
    def test_a_tenant_user_gains_nothing_from_a_platform_entry(self):
        groups_before = self.tenant_user.group_ids
        self._enter()
        self.assertEqual(self.tenant_user.group_ids, groups_before)
        self.assertFalse(self.tenant_user.nc_platform_proxy)

    def test_the_proxy_cannot_sign_in_with_a_password(self):
        proxy = self._enter()
        self.url_open('/ncollection/platform/exit', allow_redirects=False)
        response = self.url_open('/web/session/authenticate', headers={'Content-Type': 'application/json'},
                                 data=json.dumps({'jsonrpc': '2.0', 'method': 'call', 'params': {
                                     'db': self.env.cr.dbname, 'login': proxy.login, 'password': PASSWORD}}))
        self.assertIn('error', response.json())

    # ------------------------------------------------------------------ return / exit
    def test_exit_returns_to_the_platform_and_ends_the_session(self):
        self._enter()
        response = self.url_open('/ncollection/platform/exit', allow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers['Location'], 'https://platform.example/odoo')
        after = self.url_open('/web/dataset/call_kw/res.partner/search_count', headers={
            'Content-Type': 'application/json'}, data=json.dumps({'jsonrpc': '2.0', 'method': 'call', 'params': {
                'model': 'res.partner', 'method': 'search_count', 'args': [[]], 'kwargs': {}}}))
        self.assertIn('error', after.json(), "the session no longer reads data")

    # ------------------------------------------------------------------ multi-company: least privilege
    def _main_company(self):
        return self.env.ref('base.main_company')

    def test_the_proxy_gets_the_entered_company_only_not_every_company(self):
        # a company added later has a sequence; the original one's is NULL, which Postgres sorts LAST: the proxy
        # must still get the workspace's main company, not whichever sorts first
        second = self.env['res.company'].create({'name': 'Second Synthetic Company', 'sequence': 1})
        proxy = self._enter()
        self.assertEqual(proxy.company_ids, self._main_company())
        self.assertEqual(proxy.company_id, self._main_company())
        self.assertNotIn(second, proxy.company_ids, "another company of the tenant is not preloaded")

    def test_a_repeat_entry_resets_the_proxy_to_the_entered_company(self):
        proxy = self._enter()
        extra = self.env['res.company'].create({'name': 'Extra Synthetic Company', 'sequence': 1})
        archived = self.env['res.company'].create({'name': 'Archived Synthetic Company', 'sequence': 1})
        proxy.write({'company_ids': [(4, extra.id), (4, archived.id)]})      # granted by hand in between
        archived.action_archive()
        self.url_open('/ncollection/platform/exit', allow_redirects=False)
        again = self._enter('Later-' + NONCE)
        self.assertEqual(again, proxy)
        self.assertEqual(proxy.with_context(active_test=False).company_ids, self._main_company(),
                         "nothing granted before carries over, an archived company least of all")
        self.assertNotIn(extra, self.tenant_user.company_ids, "a tenant user's companies are not touched")

    # ------------------------------------------------------------------ refused entries are security events
    def _denials(self):
        return self.env['ncollection.auth.log'].sudo().search([('event_type', '=', 'platform_denied')])

    def test_an_unknown_or_expired_link_is_logged_as_a_refused_entry(self):
        if 'ncollection.auth.log' not in self.env:
            return
        response = self.url_open('/ncollection/platform/enter', data={'token': 'Never-Granted-' + NONCE},
                                 allow_redirects=False)
        self.assertEqual(response.status_code, 403)
        denied = self._denials()
        self.assertEqual(len(denied), 1)
        self.assertFalse(denied.user_id, "no user is signed in by a refused link")
        self.assertNotIn(NONCE, denied.login or '', "the token is never written to the log")

    def test_a_failing_audit_write_does_not_turn_a_refusal_into_a_500(self):
        """A refused entry is a bare 403 whatever happens to the log: if the audit write itself fails, the answer is
        still the same 403 (and the failure is logged), never a server error that tells a caller something."""
        if 'ncollection.auth.log' not in self.env:
            return
        log_model = type(self.env['ncollection.auth.log'])
        expected = 'odoo.addons.ncollection_core.controllers.platform_entry'      # the one logger that reports it
        with patch.object(log_model, '_capture_isolated', side_effect=RuntimeError('the log is down')), \
                mute_logger(expected):
            response = self.url_open('/ncollection/platform/enter', data={'token': 'Never-Granted-' + NONCE},
                                     allow_redirects=False)
        self.assertEqual(response.status_code, 403)
        self.assertNotEqual(response.status_code, 500)

    def test_a_used_link_is_logged_as_a_refused_entry(self):
        if 'ncollection.auth.log' not in self.env:
            return
        self._enter()
        self.url_open('/ncollection/platform/exit', allow_redirects=False)
        self.assertFalse(self._denials())
        response = self.url_open('/ncollection/platform/enter', data={'token': NONCE}, allow_redirects=False)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(len(self._denials()), 1)

    def test_a_grant_the_proxy_cannot_redeem_is_logged_under_that_proxy_login(self):
        if 'ncollection.auth.log' not in self.env:
            return
        # a real (non-proxy) user squatting the reserved login: the proxy refuses the grant
        self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Squatter', 'login': 'platform.u9', 'password': PASSWORD,
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        self.env['ncollection.platform.entry'].grant_from_platform(_grant('Squat-' + NONCE, 'u9', 'Mallory'))
        response = self.url_open('/ncollection/platform/enter', data={'token': 'Squat-' + NONCE},
                                 allow_redirects=False)
        self.assertEqual(response.status_code, 403)
        denied = self._denials()
        self.assertEqual(len(denied), 1)
        self.assertEqual(denied.login, 'platform.u9')
        self.assertFalse(self.env['ncollection.auth.log'].sudo().search([('event_type', '=', 'platform_entry')]))

    def test_a_non_proxy_session_on_the_platform_exit_is_logged_as_refused(self):
        if 'ncollection.auth.log' not in self.env:
            return
        self.authenticate('tenant_acct', PASSWORD)
        response = self.url_open('/ncollection/platform/exit', allow_redirects=False)
        self.assertEqual(response.status_code, 403)
        denied = self._denials()
        self.assertEqual(len(denied), 1)
        self.assertEqual(denied.login, 'tenant_acct')
        self.assertFalse(self.env['ncollection.auth.log'].sudo().search([('event_type', '=', 'platform_exit')]))


@tagged('post_install', '-at_install')
class TestDenialAuditBudget(HttpCase):

    def test_the_refusal_audit_is_capped_per_database_and_minute(self):
        from ..controllers import platform_entry as controller
        now = 1_000_000 * 60.0
        budget = controller._DENIAL_BUDGET_PER_MINUTE
        allowed = [controller._denial_budget_left('budgetdb', now=now) for _i in range(budget + 5)]
        self.assertEqual(sum(allowed), controller._DENIAL_BUDGET_PER_MINUTE)
        self.assertTrue(controller._denial_budget_left('budgetdb', now=now + 61), 'a new minute starts a new budget')
        self.assertTrue(controller._denial_budget_left('otherdb', now=now + 61), 'one budget per database')
        # a flood of anonymous refusals leaves the budget of the refusals that name a proxy login untouched
        later = now + 600
        for _i in range(budget + 5):
            controller._denial_budget_left('flooddb', now=later)
        self.assertTrue(controller._denial_budget_left('flooddb', named=True, now=later))
        # the signed-in /exit noise has a budget of its own too
        for _i in range(budget + 5):
            controller._denial_budget_left('flooddb', named='exit', now=later)
        self.assertTrue(controller._denial_budget_left('flooddb', named=True, now=later))
