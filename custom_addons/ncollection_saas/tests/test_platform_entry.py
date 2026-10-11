# -*- coding: utf-8 -*-
"""Platform administrator -> tenant entry ("Enter Company"), platform side.

The cross-database round trip (a real token redeemed in a real tenant database, data written to the selected
tenant only) is proven by scripts/provisioning/verify_platform_entry.sh. These tests pin the decisions the
platform makes before anything leaves it.
"""
import hashlib
import json
import os
from urllib.parse import parse_qs, urlparse
from unittest.mock import MagicMock, patch

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged

MODULE = 'odoo.addons.ncollection_saas.models.platform_entry'
PLATFORM_GROUP = 'ncollection_subscription.group_platform_admin'


def _reply(status=200, body=b'{"ok": true, "id": 1}'):
    resp = MagicMock()
    resp.status_code = status
    resp.raw.read.return_value = body
    return resp


@tagged('post_install', '-at_install')
class TestPlatformEntry(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['ir.config_parameter'].sudo().set_param('web.base.url', 'http://localhost:8069')
        group = cls.env.ref(PLATFORM_GROUP).id
        internal = cls.env.ref('base.group_user').id
        cls.alice = cls.env['res.users'].create({
            'name': 'Alice Admin', 'login': 'plat_alice', 'group_ids': [(6, 0, [internal, group])]})
        cls.bob = cls.env['res.users'].create({
            'name': 'Bob Admin', 'login': 'plat_bob', 'group_ids': [(6, 0, [internal, group])]})
        cls.clerk = cls.env['res.users'].create({
            'name': 'Clerk', 'login': 'plat_clerk', 'group_ids': [(6, 0, [internal])]})
        cls.atlas = cls._tenant('Atlas', 'atlas', cls.alice)
        cls.wasla = cls._tenant('Wasla', 'wasla', cls.alice | cls.bob)

    @classmethod
    def _tenant(cls, name, db, admins, **extra):
        return cls.env['ncollection.tenant'].with_user(cls.env.ref('base.user_root')).create(dict({
            'company_name': name, 'database_name': db, 'database_status': 'ready', 'status': 'active',
            'platform_admin_ids': [(6, 0, admins.ids)]}, **extra))

    def _enter(self, tenant, user, reply=None):
        post = MagicMock(return_value=reply or _reply())
        with patch.dict(os.environ, {'NC_CONFIG_SYNC_KEY': 'master-key-for-tests'}), \
                patch(MODULE + '.requests.post', post):
            action = tenant.with_user(user).action_enter_company()
        return action, post

    # ------------------------------------------------------------------ who may enter
    def test_an_authorised_platform_admin_enters_and_lands_on_the_tenant_host(self):
        action, post = self._enter(self.atlas, self.alice)
        url = urlparse(action['url'])
        self.assertEqual(action['type'], 'ir.actions.act_url')
        self.assertTrue(url.netloc.startswith('atlas.'), url.netloc)
        self.assertEqual(url.path, '/ncollection/platform/enter')
        self.assertEqual(url.scheme, 'http')                  # follows how the platform itself is served
        token = parse_qs(url.fragment)['token'][0]
        self.assertGreaterEqual(len(token), 40)
        self.assertEqual(post.call_count, 1)

    def test_only_the_hash_of_the_token_is_sent_to_the_tenant(self):
        action, post = self._enter(self.atlas, self.alice)
        token = parse_qs(urlparse(action['url']).fragment)['token'][0]
        _args, kwargs = post.call_args
        sent = json.loads(kwargs['data'])['vals']
        self.assertEqual(sent['token_hash'], hashlib.sha256(token.encode()).hexdigest())
        self.assertNotIn(token, kwargs['data'])
        self.assertEqual(sent['platform_name'], 'Alice Admin')
        self.assertEqual(sent['platform_ref'], 'u%d' % self.alice.id)
        self.assertEqual(set(sent), {'token_hash', 'platform_ref', 'platform_name', 'return_url', 'ttl'})
        self.assertNotIn('password', kwargs['data'].lower())

    def test_every_entry_gets_a_fresh_one_time_token(self):
        first, _ = self._enter(self.atlas, self.alice)
        second, _ = self._enter(self.atlas, self.alice)
        self.assertNotEqual(first['url'], second['url'])

    def test_a_tenant_outside_the_allow_list_cannot_be_entered(self):
        action, post = None, MagicMock()
        with self.assertRaises(AccessError):
            action, post = self._enter(self.atlas, self.bob)          # Bob is only on Wasla
        self.assertFalse(post.called)
        self.assertIsNone(action)

    def test_a_non_platform_user_cannot_enter_even_if_listed_by_hand(self):
        self.atlas.sudo().write({'platform_admin_ids': [(4, self.clerk.id)]})
        with self.assertRaises(AccessError):
            self._enter(self.atlas, self.clerk)

    def test_each_platform_admin_sees_only_the_companies_they_are_authorised_for(self):
        Tenant = self.env['ncollection.tenant']
        self.assertEqual(Tenant._nc_enterable_for(self.alice), self.atlas | self.wasla)
        self.assertEqual(Tenant._nc_enterable_for(self.bob), self.wasla)
        self.assertFalse(Tenant._nc_enterable_for(self.clerk))
        summary = self.env['ncollection.subscription.dashboard'].with_user(self.bob).create({})
        self.assertEqual(summary.enterable_tenant_ids, self.wasla)

    # ------------------------------------------------------------------ what may be entered
    def test_a_tenant_that_is_suspended_or_not_ready_cannot_be_entered(self):
        for vals in ({'status': 'suspended'}, {'status': 'expired'}, {'database_status': 'not_provisioned'}):
            tenant = self._tenant('Paused', 'paused%d' % abs(hash(str(vals)) % 1000), self.alice)
            tenant.sudo().write(vals)
            with self.assertRaises(UserError, msg=str(vals)):
                self._enter(tenant, self.alice)

    def test_the_target_database_comes_from_the_registry_never_from_the_caller(self):
        """There is no argument that could name a database; and a registry value that is not a legal tenant key
        (injection, uppercase, path) is refused before anything is sent."""
        for i, bad in enumerate(('atlas;drop', 'ATLAS', 'a/b', '../x', 'a b', '')):
            tenant = self._tenant('Odd', 'odd%d' % i, self.alice)
            # Written around the model's own name checks on purpose: this is the last line of defence.
            self.env.cr.execute(
                'UPDATE ncollection_tenant SET database_name = %s WHERE id = %s', [bad or None, tenant.id])
            tenant.invalidate_recordset(['database_name'])
            with self.assertRaises(UserError, msg=bad):
                self._enter(tenant, self.alice)
        _action, post = self._enter(self.atlas, self.alice)
        headers = post.call_args[1]['headers']
        self.assertTrue(headers['Host'].startswith('atlas.'))
        self.assertEqual(headers['X-Odoo-Database'], 'atlas')

    def test_each_tenant_is_reached_with_its_own_derived_key(self):
        _a, post_a = self._enter(self.atlas, self.alice)
        _w, post_w = self._enter(self.wasla, self.alice)
        auth_a = post_a.call_args[1]['headers']['Authorization']
        auth_w = post_w.call_args[1]['headers']['Authorization']
        self.assertNotEqual(auth_a, auth_w)

    # ------------------------------------------------------------------ failure and attribution
    def test_a_tenant_that_refuses_the_grant_fails_loudly_and_opens_nothing(self):
        with self.assertRaises(UserError):
            self._enter(self.atlas, self.alice, reply=_reply(status=403, body=b''))
        with self.assertRaises(UserError):
            self._enter(self.atlas, self.alice, reply=_reply(body=b'{"ok": false}'))

    def test_without_the_platform_service_key_nothing_is_attempted(self):
        post = MagicMock()
        env = {k: v for k, v in os.environ.items() if k != 'NC_CONFIG_SYNC_KEY'}
        with patch.dict(os.environ, env, clear=True), patch(MODULE + '.requests.post', post):
            with self.assertRaises(UserError):
                self.atlas.with_user(self.alice).action_enter_company()
        self.assertFalse(post.called)

    def test_the_entry_is_attributed_to_the_real_platform_admin(self):
        self._enter(self.atlas, self.alice)
        message = self.atlas.message_ids[:1]
        self.assertEqual(message.author_id, self.alice.partner_id)
        self.assertIn('Platform Administrator', message.body)

    def test_creating_a_tenant_authorises_its_creator_but_not_the_other_platform_admins(self):
        creator = self.env['res.users'].create({
            'name': 'Cara Creator', 'login': 'plat_cara', 'group_ids': [(6, 0, [
                self.env.ref('base.group_user').id, self.env.ref('base.group_system').id])]})
        tenant = self.env['ncollection.tenant'].with_user(creator).create({
            'company_name': 'Cara Co', 'database_name': 'caraco'})
        self.assertEqual(tenant.platform_admin_ids, creator)
        self.assertNotIn(self.bob, tenant.platform_admin_ids)

    def test_a_platform_admin_cannot_add_themselves_to_a_tenant_allow_list(self):
        """The allow-list is worthless if the people it restricts can edit it: only an Administrator can."""
        with self.assertRaises(AccessError):
            self.atlas.with_user(self.bob).write({'platform_admin_ids': [(4, self.bob.id)]})
        with self.assertRaises(AccessError):
            self.env['ncollection.tenant'].with_user(self.bob).create({
                'company_name': 'Self granted', 'database_name': 'selfgrant', 'platform_admin_ids': [(4, self.bob.id)]})
        self.assertNotIn(self.bob, self.atlas.platform_admin_ids)
        admin = self.env['res.users'].create({
            'name': 'Settings admin', 'login': 'plat_settings', 'group_ids': [(6, 0, [
                self.env.ref('base.group_user').id, self.env.ref('base.group_system').id])]})
        self.atlas.with_user(admin).write({'platform_admin_ids': [(4, self.bob.id)]})
        self.assertIn(self.bob, self.atlas.platform_admin_ids)
