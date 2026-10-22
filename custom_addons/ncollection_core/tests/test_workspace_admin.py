# -*- coding: utf-8 -*-
"""Workspace roles, user administration and its audit: real groups, real guards, nothing secret in the record.

Roles are a view of the groups a user holds, so these tests assert on ``res.groups`` / ``all_group_ids`` (what Odoo
enforces), never on a stored role assignment. Synthetic users only; passwords are throwaway values made per run.
"""
import secrets
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessDenied, AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged


def _password():
    return 'Aa1!' + secrets.token_urlsafe(18)


@tagged('post_install', '-at_install')
class _Common(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Users = cls.env['res.users'].with_context(no_reset_password=True)
        cls.Role = cls.env['ncollection.role']
        cls.owner_group = cls.env.ref('ncollection_core.group_role_owner')
        cls.admin_user = cls.Users.create({
            'name': 'Synthetic Administrator', 'login': 'synth_admin',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id, cls.owner_group.id])]})
        cls.second_admin = cls.Users.create({
            'name': 'Synthetic Second Administrator', 'login': 'synth_admin2',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id, cls.owner_group.id])]})
        cls.ordinary = cls.Users.create({
            'name': 'Synthetic Ordinary', 'login': 'synth_ordinary',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])]})
        cls.member = cls.Users.create({
            'name': 'Synthetic Member', 'login': 'synth_member',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])]})

    def _role(self, code):
        return self.Role.search([('code', '=', code)], limit=1)

    def _custom_role(self, name='Synthetic role', groups=None):
        groups = groups if groups is not None else self.env.ref('base.group_partner_manager')
        return self.Role.with_user(self.admin_user).create({'name': name, 'custom_group_ids': [(6, 0, groups.ids)]})


class TestStandardRoles(_Common):

    def test_the_standard_roles_are_provided_and_map_to_real_groups(self):
        codes = set(self.Role.search([('kind', '=', 'standard')]).mapped('code'))
        self.assertTrue({'administrator', 'finance_manager', 'accountant', 'sales_manager', 'sales_user',
                         'purchase_manager', 'purchase_user', 'inventory_manager', 'inventory_user', 'hr_manager',
                         'hr_user', 'crm_manager', 'crm_user', 'reports_viewer'} <= codes)
        admin = self._role('administrator')
        self.assertEqual(admin.group_ids, self.owner_group)
        self.assertTrue(admin.grants_administration)
        self.assertFalse(self._role('accountant').grants_administration)

    def test_a_role_needing_an_application_that_is_absent_says_so(self):
        role = self.Role.sudo().create({'name': 'Needs a ghost', 'code': 'ghost', 'kind': 'standard',
                                        'group_xmlids': 'ncollection_core.group_role_employee,ghost_app.group_ghost'})
        self.assertFalse(role.available)
        self.assertEqual(role.missing_apps, 'ghost_app')
        with self.assertRaisesRegex(UserError, 'ghost_app'):
            self.member.with_user(self.admin_user).nc_set_roles(role.ids)

    def test_a_standard_role_can_be_renamed_not_regrouped_not_deleted(self):
        role = self._role('accountant').with_user(self.admin_user)
        role.write({'name': 'Bookkeeper'})
        self.assertEqual(role.name, 'Bookkeeper')
        with self.assertRaises(AccessError):
            role.write({'group_xmlids': 'base.group_system'})
        with self.assertRaises(AccessError):
            role.unlink()

    def test_an_ordinary_user_cannot_manage_roles(self):
        with self.assertRaises(AccessError):
            self.Role.with_user(self.member).create({'name': 'Mine', 'custom_group_ids': [(6, 0, [])]})
        with self.assertRaises(AccessError):
            self._role('accountant').with_user(self.member).write({'active': False})


class TestCustomRoles(_Common):

    def test_a_custom_role_grants_the_groups_it_names(self):
        role = self._custom_role()
        self.assertEqual(role.kind, 'custom')
        self.assertTrue(role.code.startswith('custom_'))
        self.assertEqual(role.group_ids, self.env.ref('base.group_partner_manager'))
        self.assertTrue(role.available)

    def test_a_custom_role_can_never_carry_administration(self):
        for xmlid in ('base.group_system', 'base.group_erp_manager', 'ncollection_core.group_role_owner',
                      'base.group_portal', 'ncollection_core.group_config_sync'):
            with self.subTest(group=xmlid), self.assertRaises(ValidationError):
                self._custom_role('Sneaky ' + xmlid, groups=self.env.ref(xmlid))

    def test_a_custom_role_cannot_reach_administration_through_an_implied_group(self):
        """The check is on the transitive closure: a group that IMPLIES system is as forbidden as system itself."""
        implying = self.env['res.groups'].sudo().create({
            'name': 'Synthetic implies system', 'implied_ids': [(4, self.env.ref('base.group_system').id)]})
        with self.assertRaises(ValidationError):
            self._custom_role('Via implied', groups=implying)

    def test_a_custom_role_needs_a_group(self):
        with self.assertRaises(ValidationError):
            self._custom_role('Empty', groups=self.env['res.groups'])

    def test_deleting_a_role_removes_the_name_not_anyones_access(self):
        """A role is a name for groups: deleting it cannot take access away from (or give it to) anybody."""
        role = self._custom_role()
        self.member.with_user(self.admin_user).nc_set_roles(role.ids)
        partner_manager = self.env.ref('base.group_partner_manager')
        role.with_user(self.admin_user).unlink()
        self.assertFalse(role.exists())
        self.assertIn(partner_manager, self.member.all_group_ids)
        self.assertFalse(self.member.nc_role_ids)


class TestRoleAssignment(_Common):

    def test_giving_a_role_adds_its_real_groups_and_taking_it_removes_them(self):
        role = self._custom_role()
        partner_manager = self.env.ref('base.group_partner_manager')
        self.assertNotIn(partner_manager, self.member.all_group_ids)
        self.member.with_user(self.admin_user).nc_set_roles(role.ids)
        self.assertIn(partner_manager, self.member.group_ids)
        self.assertEqual(self.member.nc_role_ids, role)
        self.assertEqual(self.member.nc_role_summary, role.name)
        self.member.with_user(self.admin_user).nc_set_roles([])
        self.assertNotIn(partner_manager, self.member.all_group_ids)
        self.assertFalse(self.member.nc_role_ids)

    def test_an_ordinary_user_cannot_even_attempt_a_role_change(self):
        with self.assertRaises(AccessError):
            self.member.with_user(self.ordinary).nc_set_roles([])

    def test_the_roles_field_is_the_same_thing_as_the_method(self):
        role = self._custom_role()
        self.member.with_user(self.admin_user).write({'nc_role_ids': [(6, 0, role.ids)]})
        self.assertIn(self.env.ref('base.group_partner_manager'), self.member.group_ids)

    def test_a_group_two_roles_share_stays_while_one_of_them_is_held(self):
        shared = self.env.ref('base.group_partner_manager')
        contact = self.env.ref('base.group_no_one')
        first = self._custom_role('First', shared)
        second = self._custom_role('Second', shared | contact)
        self.member.with_user(self.admin_user).nc_set_roles((first | second).ids)
        self.member.with_user(self.admin_user).nc_set_roles(second.ids)
        self.assertIn(shared, self.member.group_ids)
        self.member.with_user(self.admin_user).nc_set_roles([])
        self.assertNotIn(shared, self.member.all_group_ids)

    def test_the_most_specific_role_is_the_one_shown(self):
        manager = self._role('sales_manager')
        user_role = self._role('sales_user')
        self.assertTrue(manager.available and user_role.available, "Sales must be installed for this check")
        self.member.with_user(self.admin_user).nc_set_roles(manager.ids)
        self.assertIn(user_role, self.member._nc_held_roles())
        self.assertEqual(self.member.nc_role_ids, manager)

    def test_only_an_administrator_gives_administration(self):
        admin_role = self._role('administrator')
        manager = self.Users.create({
            'name': 'Synthetic Access Manager', 'login': 'synth_access',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id, self.env.ref('base.group_erp_manager').id])]})
        with self.assertRaises(AccessError):
            self.member.with_user(manager).nc_set_roles(admin_role.ids)
        self.member.with_user(self.admin_user).nc_set_roles(admin_role.ids)
        self.assertIn(self.owner_group, self.member.all_group_ids)

    def test_nobody_hands_out_a_group_they_do_not_hold(self):
        manager = self.Users.create({
            'name': 'Synthetic Access Manager 2', 'login': 'synth_access2',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id, self.env.ref('base.group_erp_manager').id])]})
        # a group nobody here holds, whatever applications this database has installed
        rare = self.env['res.groups'].sudo().create({'name': 'Synthetic rare rights'})
        role = self._custom_role(groups=rare)
        with self.assertRaises(AccessError):
            self.member.with_user(manager).nc_set_roles(role.ids)

    def test_the_last_administrator_cannot_lose_administration(self):
        # every other administrator in this database, including the demo / bootstrap ones, is archived for the test
        others = self.Users.sudo().search([
            ('all_group_ids', 'in', self.owner_group.id), ('id', '!=', self.admin_user.id)])
        others.sudo().write({'active': False})
        self.assertEqual(self.Users.sudo().search_count(
            [('all_group_ids', 'in', self.owner_group.id), ('active', '=', True)]), 1)
        with self.assertRaisesRegex(ValidationError, 'last workspace Administrator'):
            self.admin_user.with_user(self.admin_user).nc_set_roles([])

    def test_roles_apply_to_workspace_users_only(self):
        portal = self.Users.create({'name': 'Synthetic Portal', 'login': 'synth_portal',
                                    'group_ids': [(6, 0, [self.env.ref('base.group_portal').id])]})
        with self.assertRaises(UserError):
            portal.with_user(self.admin_user).nc_set_roles(self._custom_role().ids)

    def test_the_access_matrix_is_read_from_the_access_rows(self):
        role = self._custom_role(groups=self.env.ref('base.group_partner_manager'))
        rows = {row['model']: row for row in role._nc_access_matrix()}
        partner = rows['res.partner']
        access = self.env['ir.model.access'].sudo().search([
            ('model_id.model', '=', 'res.partner'), ('group_id', 'in', role.group_ids.all_implied_ids.ids)])
        self.assertEqual(partner['write'], any(access.mapped('perm_write')))
        self.assertEqual(partner['unlink'], any(access.mapped('perm_unlink')))
        action = role.with_user(self.admin_user).action_view_access_matrix()
        self.assertEqual(action['res_model'], 'ncollection.role.access.line')
        self.assertTrue(self.env['ncollection.role.access.line'].search(action['domain']))


class TestPasswordReset(_Common):

    def _sign_in(self, user, password):
        """True when ``password`` is accepted for ``user`` (the credential check Odoo's login runs)."""
        try:
            user.with_user(user.id)._check_credentials(
                {'type': 'password', 'password': password}, {'interactive': True})
        except AccessDenied:
            return False
        return True

    def test_a_reset_generates_a_working_temporary_password_and_forces_a_change(self):
        temporary = self.member.with_user(self.admin_user).nc_reset_password()
        self.assertTrue(temporary and len(temporary) >= 16)
        self.assertTrue(self._sign_in(self.member, temporary))
        self.assertTrue(self.member.nc_must_change_password)

    def test_an_explicit_password_is_applied_and_never_returned(self):
        chosen = _password()
        self.assertIsNone(self.member.with_user(self.admin_user).nc_reset_password(chosen, must_change=False))
        self.assertTrue(self._sign_in(self.member, chosen))
        self.assertFalse(self.member.nc_must_change_password)

    def test_a_short_password_is_refused(self):
        with self.assertRaises(UserError):
            self.member.with_user(self.admin_user).nc_reset_password('Short1!')

    def test_a_reset_is_audited_without_the_password(self):
        chosen = _password()
        temporary = self.member.with_user(self.admin_user).nc_reset_password()
        self.member.with_user(self.admin_user).nc_reset_password(chosen)
        rows = self.env['ncollection.auth.log'].sudo().search([
            ('event_type', '=', 'admin_password_reset'), ('target_user_id', '=', self.member.id)])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows.mapped('user_id'), self.admin_user)
        for row in rows:
            for secret in (temporary, chosen):
                for text in (row.detail or '', row.login or '', row.actor_ref or ''):
                    self.assertNotIn(secret, text)

    def test_the_stored_password_is_a_hash_and_nothing_else_holds_it(self):
        temporary = self.member.with_user(self.admin_user).nc_reset_password()
        self.env.cr.execute("SELECT password FROM res_users WHERE id = %s", [self.member.id])
        self.assertNotEqual(self.env.cr.fetchone()[0], temporary)
        for model in ('ncollection.auth.log', 'mail.message'):
            self.env.cr.execute("SELECT 1 FROM %s WHERE CAST(%s AS text) LIKE %%s LIMIT 1" % (
                self.env[model]._table, 'detail' if model == 'ncollection.auth.log' else 'body'),
                ['%' + temporary + '%'])
            self.assertFalse(self.env.cr.fetchone(), model)

    def test_only_an_administrator_resets_an_administrators_password(self):
        manager = self.Users.create({
            'name': 'Synthetic Access Manager 3', 'login': 'synth_access3',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id, self.env.ref('base.group_erp_manager').id])]})
        with self.assertRaises(AccessError):
            self.second_admin.with_user(manager).nc_reset_password()
        with self.assertRaises(AccessError):
            self.member.with_user(self.member).nc_reset_password()      # nobody resets by themselves this way
        self.second_admin.with_user(self.admin_user).nc_reset_password()

    def test_a_user_manager_cannot_reset_the_password_of_someone_with_rights_they_lack(self):
        manager = self.Users.create({
            'name': 'Synthetic Access Manager 4', 'login': 'synth_access4',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id, self.env.ref('base.group_erp_manager').id])]})
        rare = self.env['res.groups'].sudo().create({'name': 'Synthetic rare rights 2'})
        target = self.Users.create({'name': 'Rare Holder', 'login': 'synth_rare',
                                    'group_ids': [(6, 0, [self.env.ref('base.group_user').id, rare.id])]})
        with self.assertRaises(AccessError):
            target.with_user(manager).nc_reset_password()
        self.member.with_user(manager).nc_reset_password()               # someone whose groups the manager holds

    def test_a_proxy_and_a_customer_are_not_reset_here(self):
        proxy = self.Users.sudo().create({'name': 'Proxy', 'login': 'platform.u99', 'nc_platform_proxy': True,
                                          'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        with self.assertRaises(UserError):
            proxy.with_user(self.admin_user).nc_reset_password()

    def test_a_platform_administrators_entry_session_cannot_set_a_password_here(self):
        """Entering a workspace as a platform administrator is no way to mint a credential that outlives the session:
        the reset goes through res.users.write, whose guard refuses a platform proxy. The platform's own Tenant
        Administration (the service channel) is the way a platform administrator resets a password."""
        proxy = self.Users.sudo().create({
            'name': 'Platform Admin: Test', 'login': 'platform.u77', 'nc_platform_proxy': True,
            'nc_platform_ref': 'u77',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id, self.owner_group.id])]})
        with self.assertRaises(AccessError):
            self.member.with_user(proxy).nc_reset_password()
        with self.assertRaises(AccessError):
            self.Users.with_user(proxy)._nc_create_workspace_user('Made By Proxy', 'made.by.proxy')

    def test_the_reset_dialog_clears_what_was_typed(self):
        chosen = _password()
        wizard = self.env['ncollection.user.reset.password'].with_user(self.admin_user).create({
            'user_id': self.member.id, 'mode': 'explicit', 'new_password': chosen})
        action = wizard.action_apply()
        self.assertEqual(action['tag'], 'display_notification')
        self.assertFalse(wizard.new_password)
        self.assertNotIn(chosen, str(action))
        self.assertTrue(self._sign_in(self.member, chosen))

    def test_the_temporary_password_is_shown_to_the_administrator_only_in_the_dialog_result(self):
        wizard = self.env['ncollection.user.reset.password'].with_user(self.admin_user).create(
            {'user_id': self.member.id})
        action = wizard.action_apply()
        message = action['params']['message']
        self.assertIn(self.member.name, message)
        self.assertTrue(action['params']['sticky'])


class TestActivationAndCreation(_Common):

    def test_only_an_administrator_activates_a_user(self):
        self.member.with_user(self.admin_user).nc_set_active(False)
        with self.assertRaises(AccessError):
            self.member.with_user(self.ordinary).nc_set_active(True)
        with self.assertRaises(AccessError):
            self.member.with_user(self.ordinary).action_nc_activate()
        self.assertFalse(self.member.active)
        self.member.with_user(self.admin_user).action_nc_activate()
        self.assertTrue(self.member.active)

    def test_the_pending_change_flag_cannot_be_cleared_over_rpc(self):
        self.assertFalse(hasattr(self.Users, 'nc_clear_must_change'), 'it must not be a public (RPC-callable) name')

    def test_a_reset_revokes_the_users_api_keys(self):
        self.env['res.users.apikeys'].with_user(self.member)._generate(
            'rpc', 'Synthetic key', fields.Datetime.now() + timedelta(days=1))
        self.assertTrue(self.env['res.users.apikeys'].sudo().search([('user_id', '=', self.member.id)]))
        self.member.with_user(self.admin_user).nc_reset_password()
        self.assertFalse(self.env['res.users.apikeys'].sudo().search([('user_id', '=', self.member.id)]))

    def test_the_handed_over_password_is_refused_on_the_sessionless_rpc_path_until_changed(self):
        chosen = _password()
        self.member.with_user(self.admin_user).nc_reset_password(chosen, must_change=True)
        with self.assertRaises(AccessDenied):
            self.member.with_user(self.member.id)._check_credentials(
                {'type': 'password', 'password': chosen}, {'interactive': False})
        # the web sign-in page (interactive) is how the person gets to choose their own
        self.member.with_user(self.member.id)._check_credentials(
            {'type': 'password', 'password': chosen}, {'interactive': True})

    def test_a_name_and_an_email_can_be_updated_and_nothing_else(self):
        self.member.with_user(self.admin_user).nc_update_profile(name='Renamed Member', email='renamed@example.com')
        self.assertEqual((self.member.name, self.member.email), ('Renamed Member', 'renamed@example.com'))
        self.assertEqual(self.member.login, 'synth_member')
        row = self.env['ncollection.auth.log'].sudo().search([
            ('event_type', '=', 'admin_user_update'), ('target_user_id', '=', self.member.id)])
        self.assertTrue(row)
        with self.assertRaises(UserError):
            self.member.with_user(self.admin_user).nc_update_profile(email='not-an-email')
        with self.assertRaises(UserError):
            self.member.with_user(self.admin_user).nc_update_profile(name='  ')
        with self.assertRaises(AccessError):
            self.member.with_user(self.ordinary).nc_update_profile(name='Hijacked')

    def test_a_login_must_be_a_username(self):
        for bad in ('x', 'name with space', 'a@b.example', 'platform@ncollection.internal'):
            with self.subTest(login=bad), self.assertRaises(UserError):
                self.Users.with_user(self.admin_user)._nc_create_workspace_user('Bad Login', bad)

    def test_deactivation_keeps_its_guards_and_is_audited(self):
        self.member.with_user(self.admin_user).nc_set_active(False)
        self.assertFalse(self.member.active)
        self.assertTrue(self.env['ncollection.auth.log'].sudo().search_count([
            ('event_type', '=', 'admin_user_deactivate'), ('target_user_id', '=', self.member.id)]))
        with self.assertRaises(ValidationError):
            self.admin_user.with_user(self.admin_user).nc_set_active(False)          # not yourself
        self.member.with_user(self.admin_user).nc_set_active(True)
        self.assertTrue(self.member.active)

    def test_a_user_is_created_with_roles_a_temporary_password_and_an_audit_row(self):
        role = self._custom_role()
        user, temporary = self.Users.with_user(self.admin_user)._nc_create_workspace_user(
            'Created Person', 'created.person', email='created@example.com', role_ids=role.ids)
        self.assertTrue(temporary)
        self.assertEqual(user.nc_role_ids, role)
        self.assertTrue(user.nc_must_change_password)
        self.assertTrue(self.env['ncollection.auth.log'].sudo().search_count([
            ('event_type', '=', 'admin_user_create'), ('target_user_id', '=', user.id)]))

    def test_creation_respects_the_plan_seat_limit(self):
        config = self.env['ncollection.workspace.config'].sudo().get_config() or \
            self.env['ncollection.workspace.config'].sudo().create({})
        active = self.Users.sudo().search_count([('active', '=', True), ('share', '=', False),
                                                 ('nc_platform_proxy', '=', False)])
        config.sudo().write({'max_users': active})
        with self.assertRaises(ValidationError):
            self.Users.with_user(self.admin_user)._nc_create_workspace_user('Over Limit', 'over.limit')

    def test_a_taken_username_and_an_email_login_are_refused(self):
        with self.assertRaises(ValidationError):
            self.Users.with_user(self.admin_user)._nc_create_workspace_user('Twin', 'synth_member')
        with self.assertRaises(UserError):
            self.Users.with_user(self.admin_user)._nc_create_workspace_user('Mail Login', 'someone@example.com')
