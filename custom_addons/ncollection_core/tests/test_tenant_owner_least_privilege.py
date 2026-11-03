# -*- coding: utf-8 -*-
"""Option A: the tenant Owner is a business administrator, not a platform technical administrator.

The Owner holds ``group_tenant_admin`` (the Workspace Administration Center) but NOT ``base.group_system`` /
``base.group_erp_manager``, so every server-side code-execution and security-editing surface is closed AT THE ORM,
not by a per-method guard. The platform administrator and the superuser keep everything, because
``base.group_system`` implies ``group_tenant_admin``.
"""
from unittest.mock import patch

from odoo import api
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged

OWNER = 'base.group_user,ncollection_core.group_role_owner'


@tagged('post_install', '-at_install')
class TestTenantOwnerIsNotSystem(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.owner = new_test_user(cls.env, login='lp_owner', groups=OWNER)
        cls.clerk = new_test_user(cls.env, login='lp_clerk', groups='base.group_user')

    def test_the_owner_holds_workspace_admin_but_not_the_technical_super_groups(self):
        self.assertTrue(self.owner.has_group('ncollection_core.group_tenant_admin'))
        self.assertFalse(self.owner.has_group('base.group_system'))
        self.assertFalse(self.owner.has_group('base.group_erp_manager'))

    def test_base_group_system_still_implies_workspace_admin(self):
        # the platform administrator, `admin` and the superuser keep every workspace-admin right
        system = self.env.ref('base.group_system')
        tenant_admin = self.env.ref('ncollection_core.group_tenant_admin')
        self.assertIn(tenant_admin, system.all_implied_ids)
        self.assertTrue(self.env['res.users'].browse(1).has_group('ncollection_core.group_tenant_admin'))

    def test_the_owner_still_administers_the_workspace(self):
        # the Workspace Administration Center models stay readable/writable for the Owner
        self.assertTrue(self.env['ncollection.role'].with_user(self.owner).search_count([]) >= 0)
        self.env['ncollection.role'].with_user(self.owner).check_access('read')
        self.env['ncollection.workspace.overview'].with_user(self.owner).check_access('read')


@tagged('post_install', '-at_install')
class TestOwnerCannotExecuteCodeOrEditSecurity(TransactionCase):
    """Every escalation surface the earlier audit demonstrated is now refused at creation — no server-executed code
    can even be STORED, which is why Option A needs no per-method code guard."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.owner = new_test_user(cls.env, login='lp_owner2', groups=OWNER)
        cls.partner_model = cls.env.ref('base.model_res_partner').id

    def _as_owner(self, model):
        return self.env[model].with_user(self.owner)

    def test_a_server_action_with_python_code_cannot_be_created(self):
        with self.assertRaises(AccessError):
            self._as_owner('ir.actions.server').create({
                'name': 'x', 'model_id': self.partner_model, 'state': 'code',
                'code': "env['ir.module.module'].sudo().search([]).button_install()"})

    def test_a_qweb_view_cannot_be_created(self):
        with self.assertRaises(AccessError):
            self._as_owner('ir.ui.view').create({
                'name': 'x', 'type': 'qweb', 'key': 'lp.x', 'arch': '<t t-name="lp.x"/>'})

    def test_a_record_rule_cannot_be_created_or_changed(self):
        with self.assertRaises(AccessError):
            self._as_owner('ir.rule').create({
                'name': 'x', 'model_id': self.partner_model, 'domain_force': '[(1, "=", 1)]'})

    def test_security_groups_cannot_be_created_or_written(self):
        with self.assertRaises(AccessError):
            self._as_owner('res.groups').create({'name': 'x'})
        with self.assertRaises(AccessError):
            self.env.ref('ncollection_core.group_tenant_admin').with_user(self.owner).write({'name': 'x'})

    def test_acl_rows_cannot_be_written(self):
        acl = self.env['ir.model.access'].search([], limit=1)
        with self.assertRaises(AccessError):
            acl.with_user(self.owner).write({'perm_unlink': True})

    def test_a_module_cannot_be_installed(self):
        module = self.env['ir.module.module'].search([('state', '=', 'uninstalled')], limit=1)
        if module:
            with self.assertRaises(AccessError):
                module.with_user(self.owner).button_immediate_install()

    def test_the_owner_cannot_grant_itself_the_technical_groups(self):
        system = self.env.ref('base.group_system')
        with self.assertRaises(AccessError):
            self.owner.with_user(self.owner).write({'group_ids': [(4, system.id)]})
        self.assertFalse(self.owner.has_group('base.group_system'))


@tagged('post_install', '-at_install')
class TestOwnerUserManagement(TransactionCase):
    """The Owner administers users and roles end to end through the Admin Center's delegated methods; a role can never
    carry a technical group, a direct group write is refused, and the platform entry proxy is not theirs to manage."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.owner = new_test_user(cls.env, login='um_owner', groups=OWNER)
        cls.Users = cls.env['res.users']
        cls.Role = cls.env['ncollection.role']
        cls.partner_manager = cls.env.ref('base.group_partner_manager')

    def test_create_assign_deactivate_and_reactivate(self):
        user, temporary = self.Users.with_user(self.owner)._nc_create_workspace_user('Member One', 'member.one')
        self.assertTrue(temporary, "a generated temporary password is returned once")
        self.assertTrue(user.nc_must_change_password)
        role = self.Role.with_user(self.owner).create({
            'name': 'Contacts Manager', 'custom_group_ids': [(6, 0, self.partner_manager.ids)]})
        user.with_user(self.owner).nc_set_roles(role.ids)
        self.assertIn(self.partner_manager, user.group_ids)
        self.assertFalse(user.has_group('base.group_system'))
        user.with_user(self.owner).nc_set_active(False)
        self.assertFalse(user.active)
        user.with_user(self.owner).nc_set_active(True)
        self.assertTrue(user.active)

    def test_a_role_can_never_carry_a_technical_group(self):
        for xmlid in ('base.group_system', 'base.group_erp_manager', 'ncollection_core.group_tenant_admin'):
            with self.subTest(group=xmlid), self.assertRaises(ValidationError):
                self.Role.with_user(self.owner).create({
                    'name': 'Escalate %s' % xmlid, 'custom_group_ids': [(6, 0, self.env.ref(xmlid).ids)]})

    def test_a_direct_group_write_is_refused(self):
        member = new_test_user(self.env, login='um_member', groups='base.group_user')
        with self.assertRaises(AccessError):
            member.with_user(self.owner).write({'group_ids': [(4, self.partner_manager.id)]})
        self.assertNotIn(self.partner_manager, member.group_ids)

    def test_the_platform_entry_proxy_is_not_managed_by_the_owner(self):
        proxy = self.Users.with_context(no_reset_password=True).create({
            'name': 'Platform Admin: test', 'login': 'um_platform_proxy', 'nc_platform_proxy': True,
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        with self.assertRaises(UserError):
            proxy.with_user(self.owner).nc_set_active(False)
        self.assertTrue(proxy.active)


def _patch_platform_group(test, group):
    """Make the platform administrator group look absent (None: a tenant database) or present (a platform one)."""
    original = api.Environment.ref

    def ref(env, xmlid, raise_if_not_found=True):
        if xmlid == 'ncollection_subscription.group_platform_admin':
            return group if group is not None else env['res.groups']
        return original(env, xmlid, raise_if_not_found)
    patcher = patch.object(api.Environment, 'ref', ref)
    patcher.start()
    test.addCleanup(patcher.stop)


@tagged('post_install', '-at_install')
class TestOwnerLeastPrivilegeOnAnExistingDatabase(TransactionCase):
    """A database created before Option A stores Owner -> base.group_system, and its registrant holds the technical
    groups directly. A fresh install never shows this; a rehearsed upgrade of a real tenant copy did (the Owner kept
    base.group_system). The shared enforcement (migration and provisioning) must leave no Owner a technical admin."""

    def test_an_old_database_is_brought_to_option_a(self):
        _patch_platform_group(self, None)
        system, owner_role = self.env.ref('base.group_system'), self.env.ref('ncollection_core.group_role_owner')
        owner_role.write({'implied_ids': [(4, system.id)]})                 # the pre-Option-A implication
        registrant = new_test_user(self.env, login='old_registrant', groups=OWNER + ',base.group_system')
        archived = new_test_user(self.env, login='old_archived', groups=OWNER + ',base.group_erp_manager')
        archived.active = False
        proxy = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Platform Admin: old', 'login': 'old_platform_proxy', 'nc_platform_proxy': True,
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id, owner_role.id, system.id])]})
        self.assertTrue(registrant.has_group('base.group_system'), "the old state really is reproduced")
        changed = self.env['res.groups']._nc_enforce_owner_least_privilege()
        self.assertEqual(set(changed), {'old_registrant', 'old_archived'})
        self.env.invalidate_all()
        self.assertNotIn(system, owner_role.implied_ids)
        for user in (registrant, archived):
            self.assertFalse(user.has_group('base.group_system'), user.login)
            self.assertFalse(user.has_group('base.group_erp_manager'), user.login)
            self.assertTrue(user.has_group('ncollection_core.group_tenant_admin'), user.login)
        self.assertTrue(proxy.has_group('base.group_system'), "the platform entry proxy keeps its rights")
        self.assertEqual(self.env['res.groups']._nc_enforce_owner_least_privilege(), [], "idempotent")

    def test_the_platform_database_is_never_touched(self):
        _patch_platform_group(self, self.env['res.groups'].create({'name': 'Platform administrators (test)'}))
        admin = new_test_user(self.env, login='plat_admin', groups=OWNER + ',base.group_system')
        self.assertEqual(self.env['res.groups']._nc_enforce_owner_least_privilege(), [])
        self.assertTrue(admin.has_group('base.group_system'))
