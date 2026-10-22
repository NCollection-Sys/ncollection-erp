# -*- coding: utf-8 -*-
"""Tenant Administration, platform side: who may act, what leaves the platform, what the platform keeps.

The round trip into a real tenant database (the service model doing the work, the audit row, the password working) is
proven live by scripts/provisioning/verify_tenant_admin.sh and by ncollection_core's own tests. These tests pin the
decisions the platform makes before anything leaves it, with the transport mocked. Synthetic users and tenants only.
"""
import json
import logging
import os
import secrets
from unittest.mock import MagicMock, patch

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged

MODULE = 'odoo.addons.ncollection_saas.models.tenant_admin'
PLATFORM_GROUP = 'ncollection_subscription.group_platform_admin'
ENV = {'NC_CONFIG_SYNC_KEY': 'master-key-for-tests'}


def _reply(payload, status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.raw.read.return_value = json.dumps(payload).encode()
    return resp


def _users_reply():
    return {'users': [
        {'id': 11, 'name': 'Tenant Admin', 'login': 'atlasadmin', 'email': '', 'active': True, 'administrator': True,
         'roles': ['Administrator'], 'role_codes': ['administrator'], 'must_change_password': False, 'last_login': ''},
        {'id': 12, 'name': 'Sara', 'login': 'sara.m', 'email': 'sara@example.com', 'active': True,
         'administrator': False, 'roles': ['Accountant'], 'role_codes': ['accountant'],
         'must_change_password': False, 'last_login': ''}],
        'max_users': 5, 'active_internal': 2}


@tagged('post_install', '-at_install')
class TestTenantAdmin(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        internal = cls.env.ref('base.group_user').id
        group = cls.env.ref(PLATFORM_GROUP).id
        cls.alice = cls.env['res.users'].create({'name': 'Alice Admin', 'login': 'plat_alice_ta',
                                                 'group_ids': [(6, 0, [internal, group])]})
        cls.bob = cls.env['res.users'].create({'name': 'Bob Admin', 'login': 'plat_bob_ta',
                                               'group_ids': [(6, 0, [internal, group])]})
        cls.clerk = cls.env['res.users'].create({'name': 'Clerk', 'login': 'plat_clerk_ta',
                                                 'group_ids': [(6, 0, [internal])]})
        Tenant = cls.env['ncollection.tenant'].with_user(cls.env.ref('base.user_root'))
        cls.atlas = Tenant.create({'company_name': 'Atlas', 'database_name': 'atlasta', 'database_status': 'ready',
                                   'status': 'active', 'admin_login': 'atlasadmin',
                                   'platform_admin_ids': [(6, 0, cls.alice.ids)]})
        cls.unbuilt = Tenant.create({'company_name': 'Unbuilt', 'database_name': 'unbuiltta',
                                     'database_status': 'not_provisioned', 'status': 'trial',
                                     'platform_admin_ids': [(6, 0, cls.alice.ids)]})

    def _call(self, tenant, user, method, payload=None, reply=None):
        post = MagicMock(return_value=reply or _reply({'ok': True}))
        with patch.dict(os.environ, ENV), patch(MODULE + '.requests.post', post):
            result = tenant.with_user(user)._nc_admin_call(method, payload)
        return result, post

    # ------------------------------------------------------------------ authorisation (before anything leaves)
    def test_only_an_authorised_platform_administrator_can_reach_a_tenant(self):
        for user, why in ((self.bob, 'not on the allow-list'), (self.clerk, 'not a platform administrator')):
            with self.subTest(why), self.assertRaises(AccessError):
                post = MagicMock()
                with patch.dict(os.environ, ENV), patch(MODULE + '.requests.post', post):
                    self.atlas.with_user(user)._nc_admin_call('list_users')
                self.assertFalse(post.called)
        self._call(self.atlas, self.alice, 'list_users')

    def test_nothing_is_sent_for_a_tenant_without_a_workspace(self):
        post = MagicMock()
        with patch.dict(os.environ, ENV), patch(MODULE + '.requests.post', post), self.assertRaises(UserError):
            self.unbuilt.with_user(self.alice)._nc_admin_call('list_users')
        self.assertFalse(post.called)

    def test_the_target_database_is_the_registered_one_and_the_bearer_is_that_tenants(self):
        _result, post = self._call(self.atlas, self.alice, 'list_users')
        args, kwargs = post.call_args
        self.assertTrue(args[0].endswith('/json/2/ncollection.admin.service/list_users'))
        self.assertEqual(kwargs['headers']['X-Odoo-Database'], 'atlasta')
        self.assertTrue(kwargs['headers']['Host'].startswith('atlasta.'))
        self.assertTrue(kwargs['headers']['Authorization'].startswith('Bearer '))
        self.assertFalse(kwargs['allow_redirects'])

    def test_the_request_names_the_platform_administrator_who_made_it(self):
        _result, post = self._call(self.atlas, self.alice, 'reset_password', {'user_id': 12})
        sent = json.loads(post.call_args.kwargs['data'])['vals']
        self.assertEqual(sent['actor'], {'ref': 'u%d' % self.alice.id, 'name': 'Alice Admin'})
        self.assertEqual(sent['user_id'], 12)

    def test_a_missing_service_key_is_a_clear_error_not_a_request(self):
        post = MagicMock()
        with patch.dict(os.environ, {}, clear=False), patch(MODULE + '.requests.post', post):
            os.environ.pop('NC_CONFIG_SYNC_KEY', None)
            with self.assertRaises(UserError):
                self.atlas.with_user(self.alice)._nc_admin_call('list_users')
        self.assertFalse(post.called)

    # ------------------------------------------------------------------ errors are safe to show
    def test_a_refusal_from_the_tenant_shows_its_message_and_nothing_else(self):
        body = {'name': 'odoo.exceptions.ValidationError',
                'message': 'You cannot remove the last workspace Administrator.',
                'debug': 'Traceback ... secret internals ...'}
        with self.assertRaises(UserError) as caught:
            self._call(self.atlas, self.alice, 'set_active', {'user_id': 11, 'active': False}, reply=_reply(body, 422))
        self.assertIn('last workspace Administrator', str(caught.exception))
        self.assertNotIn('Traceback', str(caught.exception))

    def test_other_failures_are_generic(self):
        body = {'name': 'builtins.KeyError', 'message': "KeyError: 'secret internals'"}
        with self.assertRaises(UserError) as caught:
            self._call(self.atlas, self.alice, 'list_users', reply=_reply(body, 500))
        self.assertNotIn('secret internals', str(caught.exception))
        with self.assertRaises(UserError) as outdated:
            self._call(self.atlas, self.alice, 'list_users', reply=_reply({}, 404))
        self.assertIn('out of date', str(outdated.exception))

    # ------------------------------------------------------------------ the screens
    def test_the_user_list_marks_the_tenant_administrator_and_keeps_no_secret(self):
        post = MagicMock(return_value=_reply(_users_reply()))
        with patch.dict(os.environ, ENV), patch(MODULE + '.requests.post', post):
            action = self.atlas.with_user(self.alice).action_open_users()
        users = self.env['ncollection.tenant.users'].browse(action['res_id'])
        by_login = {line.login: line for line in users.line_ids}
        self.assertTrue(by_login['atlasadmin'].is_tenant_admin)
        self.assertFalse(by_login['sara.m'].is_tenant_admin)
        self.assertEqual(users.max_users, 5)

    def test_a_reset_dialog_is_authorised_when_it_opens_and_again_when_it_runs(self):
        with self.assertRaises(AccessError):
            self.env['ncollection.tenant.user.password'].with_user(self.bob)._nc_open(
                self.atlas.with_user(self.bob), 12, 'Sara')
        dialog = self.env['ncollection.tenant.user.password'].with_user(self.alice).create({
            'tenant_id': self.atlas.id, 'remote_id': 12, 'user_name': 'Sara'})
        # access withdrawn while the dialog was open
        self.atlas.sudo().write({'platform_admin_ids': [(6, 0, self.bob.ids)]})
        with patch.dict(os.environ, ENV), patch(MODULE + '.requests.post', MagicMock()) as post, \
                self.assertRaises(AccessError):
            dialog.action_apply()
        self.assertFalse(post.called)
        self.atlas.sudo().write({'platform_admin_ids': [(6, 0, self.alice.ids)]})

    def test_the_reset_dialog_clears_a_typed_password_and_the_platform_keeps_and_logs_none(self):
        chosen = 'Aa1!' + secrets.token_urlsafe(18)
        dialog = self.env['ncollection.tenant.user.password'].with_user(self.alice).create({
            'tenant_id': self.atlas.id, 'remote_id': 12, 'user_name': 'Sara', 'mode': 'explicit',
            'new_password': chosen})
        post = MagicMock(return_value=_reply({'ok': True, 'temporary_password': None}))
        with self.assertLogs(level=logging.DEBUG) as logs, patch.dict(os.environ, ENV), \
                patch(MODULE + '.requests.post', post):
            logging.getLogger(MODULE).info('probe')               # assertLogs needs at least one record
            action = dialog.action_apply()
        sent = json.loads(post.call_args.kwargs['data'])['vals']
        self.assertEqual(sent['password'], chosen)                # the tenant receives it...
        self.assertFalse(dialog.new_password)                     # ...the platform row no longer holds it
        self.assertNotIn(chosen, str(action))
        self.assertNotIn(chosen, '\n'.join(logs.output))
        notes = self.atlas.message_ids.mapped('body')
        self.assertTrue(notes)
        self.assertFalse([note for note in notes if chosen in (note or '')])

    def test_a_generated_password_is_shown_once_and_never_recorded(self):
        generated = 'Zz9-' + secrets.token_urlsafe(12)
        dialog = self.env['ncollection.tenant.user.password'].with_user(self.alice).create({
            'tenant_id': self.atlas.id, 'remote_id': 12, 'user_name': 'Sara'})
        post = MagicMock(return_value=_reply({'ok': True, 'temporary_password': generated}))
        with patch.dict(os.environ, ENV), patch(MODULE + '.requests.post', post):
            action = dialog.action_apply()
        self.assertIn(generated, action['params']['message'])
        self.assertTrue(action['params']['sticky'])
        for note in self.atlas.message_ids.mapped('body'):
            self.assertNotIn(generated, note or '')
        self.env.cr.execute("SELECT 1 FROM mail_message WHERE body LIKE %s", ['%' + generated + '%'])
        self.assertFalse(self.env.cr.fetchone())

    def test_editing_a_user_sends_the_name_and_email_only(self):
        dialog = self.env['ncollection.tenant.user.edit'].with_user(self.alice).create({
            'tenant_id': self.atlas.id, 'remote_id': 12, 'name': 'Sara M', 'email': 'sara.m@example.com'})
        replies = [_reply({'ok': True}), _reply(_users_reply())]
        with patch.dict(os.environ, ENV), patch(MODULE + '.requests.post', MagicMock(side_effect=replies)) as post:
            dialog.action_apply()
        sent = json.loads(post.call_args_list[0].kwargs['data'])['vals']
        self.assertEqual({k: v for k, v in sent.items() if k != 'actor'},
                         {'user_id': 12, 'name': 'Sara M', 'email': 'sara.m@example.com'})
        self.assertTrue(post.call_args_list[0].args[0].endswith('/update_user'))

    def test_changing_roles_sends_codes_only_and_is_recorded_without_secrets(self):
        options = self.env['ncollection.tenant.role.option'].create([
            {'code': 'accountant', 'name': 'Accountant', 'available': True, 'sequence': 1},
            {'code': 'sales_user', 'name': 'Sales User', 'available': True, 'sequence': 2}])
        dialog = self.env['ncollection.tenant.user.roles'].with_user(self.alice).create({
            'tenant_id': self.atlas.id, 'remote_id': 12, 'user_name': 'Sara',
            'role_option_ids': [(6, 0, options.ids)], 'available_role_ids': [(6, 0, options.ids)]})
        replies = [_reply({'ok': True, 'roles': ['Accountant', 'Sales User']}), _reply(_users_reply())]
        with patch.dict(os.environ, ENV), patch(MODULE + '.requests.post', MagicMock(side_effect=replies)) as post:
            dialog.action_apply()
        sent = json.loads(post.call_args_list[0].kwargs['data'])['vals']
        self.assertEqual(sorted(sent['role_codes']), ['accountant', 'sales_user'])
        self.assertNotIn('group_ids', sent)

    def test_a_user_is_created_through_the_tenant_and_the_platform_does_not_keep_the_login_password(self):
        dialog = self.env['ncollection.tenant.user.create'].with_user(self.alice).create({
            'tenant_id': self.atlas.id, 'name': 'New Person', 'login': 'new.person'})
        generated = 'Qq7-' + secrets.token_urlsafe(12)
        post = MagicMock(return_value=_reply(
            {'ok': True, 'user_id': 40, 'login': 'new.person', 'temporary_password': generated}))
        with patch.dict(os.environ, ENV), patch(MODULE + '.requests.post', post):
            action = dialog.action_apply()
        self.assertIn(generated, action['params']['message'])
        self.env.cr.execute("SELECT 1 FROM mail_message WHERE body LIKE %s", ['%' + generated + '%'])
        self.assertFalse(self.env.cr.fetchone())

    def test_a_clerk_cannot_use_the_models_directly(self):
        for model in ('ncollection.tenant.users', 'ncollection.tenant.user.password',
                      'ncollection.tenant.user.create'):
            with self.subTest(model), self.assertRaises(AccessError):
                self.env[model].with_user(self.clerk).search([])
