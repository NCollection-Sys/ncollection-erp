# -*- coding: utf-8 -*-
"""The platform's user-administration service: only the service account, only fixed shapes, only managed accounts.

Calls are made AS the config-sync service user (the one account the platform's bearer authenticates as), the way the
JSON-2 channel does, so the authorisation under test is the real one. Synthetic users only.
"""
import secrets

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged

ACTOR = {'ref': 'u7', 'name': 'Alice Platform'}


def _password():
    return 'Aa1!' + secrets.token_urlsafe(18)


@tagged('post_install', '-at_install')
class TestAdminService(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Users = cls.env['res.users'].with_context(no_reset_password=True)
        cls.service_user = Users.create({
            'name': 'Config Sync Service', 'login': 'sync.service@ncollection.internal',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id,
                                  cls.env.ref('ncollection_core.group_config_sync').id])]})
        cls.owner_group = cls.env.ref('ncollection_core.group_role_owner')
        cls.admin = Users.create({'name': 'Tenant Administrator', 'login': 'tenant.admin',
                                  'group_ids': [(6, 0, [cls.env.ref('base.group_user').id, cls.owner_group.id])]})
        cls.member = Users.create({'name': 'Plain Member', 'login': 'plain.member',
                                   'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])]})
        cls.ordinary = Users.create({'name': 'Ordinary Internal', 'login': 'ordinary.internal',
                                     'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])]})
        cls.service = cls.env['ncollection.admin.service'].with_user(cls.service_user)

    def _call(self, method, **vals):
        vals.setdefault('actor', dict(ACTOR))
        return getattr(self.service, method)(vals)

    # ------------------------------------------------------------------ who may call
    def test_only_the_service_account_may_call_it(self):
        for caller in (self.ordinary, self.admin, self.env.user):
            with self.subTest(caller=caller.login), self.assertRaises(AccessError):
                self.env['ncollection.admin.service'].with_user(caller).list_users({'actor': ACTOR})

    def test_a_request_must_name_the_platform_administrator_and_nothing_else(self):
        with self.assertRaises(UserError):
            self.service.list_users({})
        with self.assertRaises(UserError):
            self.service.list_users({'actor': {'ref': "u1'; drop table res_users;--"}})
        with self.assertRaises(UserError):
            self._call('list_users', group_ids=[1])                 # a key outside the contract
        with self.assertRaises(UserError):
            self._call('reset_password', user_id=self.member.id, sql='x')

    # ------------------------------------------------------------------ reads
    def test_the_user_list_has_the_administrator_marked_and_no_service_or_proxy_accounts(self):
        reply = self._call('list_users')
        by_login = {row['login']: row for row in reply['users']}
        self.assertTrue(by_login['tenant.admin']['administrator'])
        self.assertFalse(by_login['plain.member']['administrator'])
        self.assertNotIn('sync.service@ncollection.internal', by_login)
        self.assertNotIn('__system__', by_login)
        for row in reply['users']:
            self.assertFalse({'password', 'password_crypt', 'groups'} & set(row))

    def test_the_role_list_names_what_each_role_needs(self):
        roles = {row['code']: row for row in self._call('list_roles')['roles']}
        self.assertTrue(roles['administrator']['administrator'])
        self.assertIn('available', roles['sales_user'])

    # ------------------------------------------------------------------ operations
    def test_reset_password_returns_the_temporary_password_and_audits_the_platform_administrator(self):
        reply = self._call('reset_password', user_id=self.member.id)
        self.assertTrue(reply['ok'] and reply['temporary_password'])
        self.assertTrue(self.member.nc_must_change_password)
        row = self.env['ncollection.auth.log'].sudo().search([
            ('event_type', '=', 'admin_password_reset'), ('target_user_id', '=', self.member.id)], limit=1)
        self.assertIn('u7', row.actor_ref)
        self.assertIn('Alice Platform', row.actor_ref)
        self.assertNotIn(reply['temporary_password'], (row.detail or '') + (row.actor_ref or ''))

    def test_an_explicit_password_is_applied_and_not_echoed(self):
        chosen = _password()
        reply = self._call('reset_password', user_id=self.member.id, password=chosen, must_change=False)
        self.assertIsNone(reply['temporary_password'])
        self.assertNotIn(chosen, str(reply))
        self.assertFalse(self.member.nc_must_change_password)

    def test_a_client_sent_context_cannot_forge_the_platform_actor(self):
        """Only code that elevated the environment writes an actor reference; a client-sent context is ignored."""
        self.member.with_user(self.admin).with_context(nc_platform_actor='platform:u1 (Forged)').nc_reset_password()
        row = self.env['ncollection.auth.log'].sudo().search([
            ('event_type', '=', 'admin_password_reset'), ('target_user_id', '=', self.member.id)], limit=1)
        self.assertFalse(row.actor_ref)
        self.assertEqual(row.user_id, self.admin)

    def test_update_user_changes_the_name_and_email_only(self):
        reply = self._call('update_user', user_id=self.member.id, name='Renamed By Platform', email='p@example.com')
        self.assertEqual((reply['name'], reply['email']), ('Renamed By Platform', 'p@example.com'))
        self.assertEqual(self.member.login, 'plain.member')
        with self.assertRaises(UserError):
            self._call('update_user', user_id=self.member.id, name='X', login='other')          # no such key
        with self.assertRaises(UserError):
            self._call('update_user', user_id=self.service_user.id, name='Service')

    def test_a_malformed_id_or_password_is_a_clean_refusal_not_a_server_error(self):
        for bad in ('1; drop table res_users', None, True, [1], 1.5):
            with self.subTest(user_id=bad), self.assertRaises(UserError):
                self._call('reset_password', user_id=bad)
        with self.assertRaises(UserError):
            self._call('reset_password', user_id=self.member.id, password=12345678901234)
        with self.assertRaises(UserError):
            self._call('create_user', name='Bad Pw', login='bad.pw.user', password=['x'] * 12)

    def test_the_accounts_the_platform_relies_on_are_not_managed_here(self):
        with self.assertRaises(UserError):
            self._call('reset_password', user_id=self.service_user.id)
        with self.assertRaises(UserError):
            self._call('set_active', user_id=self.service_user.id, active=False)
        with self.assertRaises(UserError):
            self._call('reset_password', user_id=1)
        proxy = self.env['res.users'].sudo().create({
            'name': 'Proxy', 'login': 'platform.u9', 'nc_platform_proxy': True,
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        with self.assertRaises(UserError):
            self._call('reset_password', user_id=proxy.id)

    def test_create_user_applies_the_plan_limit_and_the_roles(self):
        reply = self._call('create_user', name='Made By Platform', login='made.by.platform',
                           role_codes=['reports_viewer'])
        user = self.env['res.users'].browse(reply['user_id'])
        self.assertTrue(reply['temporary_password'])
        self.assertTrue(user.nc_must_change_password)
        self.assertEqual(user.nc_role_ids.mapped('code'), ['reports_viewer'])
        config = self.env['ncollection.workspace.config'].sudo().get_config() or \
            self.env['ncollection.workspace.config'].sudo().create({})
        active = self.env['res.users'].sudo().search_count([
            ('active', '=', True), ('share', '=', False), ('nc_platform_proxy', '=', False)])
        config.sudo().write({'max_users': active})
        with self.assertRaises(ValidationError):
            self._call('create_user', name='One Too Many', login='one.too.many', role_codes=[])

    def test_an_unknown_role_is_refused_and_nothing_is_created(self):
        before = self.env['res.users'].sudo().with_context(active_test=False).search_count([])
        with self.assertRaises(UserError):
            self._call('create_user', name='Bad Role', login='bad.role', role_codes=['no_such_role'])
        self.assertEqual(self.env['res.users'].sudo().with_context(active_test=False).search_count([]), before)

    def test_the_platform_can_deactivate_and_activate_but_not_the_last_administrator(self):
        self._call('set_active', user_id=self.member.id, active=False)
        self.assertFalse(self.member.active)
        self._call('set_active', user_id=self.member.id, active=True)
        self.assertTrue(self.member.active)
        others = self.env['res.users'].sudo().search([('all_group_ids', 'in', self.owner_group.id),
                                                      ('id', '!=', self.admin.id)])
        others.sudo().write({'active': False})
        with self.assertRaisesRegex(ValidationError, 'last workspace Administrator'):
            self._call('set_active', user_id=self.admin.id, active=False)

    def test_the_platform_can_change_roles_and_the_last_administrator_keeps_administration(self):
        self._call('set_roles', user_id=self.member.id, role_codes=['administrator'])
        self.assertIn(self.owner_group, self.member.all_group_ids)
        self._call('set_roles', user_id=self.member.id, role_codes=[])
        self.assertNotIn(self.owner_group, self.member.all_group_ids)
        others = self.env['res.users'].sudo().search([('all_group_ids', 'in', self.owner_group.id),
                                                      ('id', '!=', self.admin.id)])
        others.sudo().write({'active': False})
        with self.assertRaisesRegex(ValidationError, 'last workspace Administrator'):
            self._call('set_roles', user_id=self.admin.id, role_codes=[])

    def test_the_platform_never_gets_the_service_account_a_user_administration_acl(self):
        """The service account keeps its narrow rights: it has no ACL on res.users or on roles beyond reading."""
        users = self.env['res.users'].with_user(self.service_user)
        with self.assertRaises(AccessError):
            users.browse(self.member.id).write({'name': 'Direct write'})
        with self.assertRaises(AccessError):
            self.env['ncollection.role'].with_user(self.service_user).create({'name': 'Direct role',
                                                                              'custom_group_ids': [(6, 0, [])]})
