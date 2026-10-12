# -*- coding: utf-8 -*-
"""The platform administrator operates ABOVE the tenant layer; a tenant Owner never does.

Two contexts, never one role: the platform administrator (group ``ncollection_subscription.group_platform_admin`` on the
platform's own database) is not subject to the TENANT rule "applications are managed by NCollection"; a tenant Owner,
who implies ``base.group_system``, is. The group does not exist in a tenant database, so no tenant user can take the
platform branch. The ORM enforces it, not a view.
"""
from unittest.mock import patch

from odoo import api
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged

PLATFORM_GROUP = 'ncollection_subscription.group_platform_admin'


@tagged('post_install', '-at_install')
class TestPlatformAdminBoundary(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Users = cls.env['res.users']
        cls.platform_group = cls.env.ref(PLATFORM_GROUP)
        base_group = cls.env.ref('base.group_user')

        def make(login, groups):
            return cls.Users.with_context(no_reset_password=True).create({
                'name': login, 'login': login, 'group_ids': [(6, 0, [base_group.id] + [g.id for g in groups])]})

        # the platform's primary administrator: platform group + system administrator
        cls.platform_admin = make('bd_platform', [cls.env.ref('base.group_system'), cls.platform_group])
        # a tenant-style Owner: the Owner role (implies base.group_system) with NO platform membership
        cls.owner = make('bd_owner', [cls.env.ref('ncollection_core.group_role_owner')])
        cls.employee = make('bd_employee', [cls.env.ref('ncollection_core.group_role_employee')])
        cls.platform_only = make('bd_platform_only', [cls.platform_group])       # platform group, not a system admin
        cls.module = cls.env['ir.module.module'].sudo().search([('name', '=', 'fleet')], limit=1)
        cls.config = cls.env['ncollection.workspace.config'].create(
            {'plan_code': 'T', 'allowed_module_names': 'stock'})

    def _as_tenant(self):
        """The database a tenant runs on: the platform group does not exist there and nothing implies it."""
        original = api.Environment.ref

        def ref(env, xmlid, raise_if_not_found=True):
            if xmlid == PLATFORM_GROUP and not raise_if_not_found:
                return env['res.groups']
            return original(env, xmlid, raise_if_not_found)
        return patch.object(api.Environment, 'ref', ref)

    def _owner_without_platform_membership(self):
        """In the platform DB base.group_system implies Platform Admin; a tenant Owner has no such path. Cut it for
        this one user to model a tenant database faithfully."""
        self.owner.write({'group_ids': [(3, self.platform_group.id)]})
        self.owner.invalidate_recordset()

    # ---------------------------------------------------------------- 1. the platform administrator
    def test_the_platform_administrator_is_recognised_and_not_blocked_by_the_tenant_rule(self):
        self.assertTrue(self.Users.with_user(self.platform_admin)._nc_actor_is_platform_admin())
        # the tenant lockout does not raise for them (the guard is the one #459 uses on every install/upgrade button)
        self.module.with_user(self.platform_admin)._nc_assert_module_management_allowed('button_immediate_install')

    def test_the_platform_administrator_can_edit_the_entitlement(self):
        self.config.with_user(self.platform_admin).write({'allowed_module_names': 'stock,fleet'})
        self.assertEqual(self.config.allowed_module_names, 'stock,fleet')

    def test_an_owner_is_never_a_platform_administrator_even_where_the_platform_group_is_implied(self):
        """Where the platform group exists, base.group_system (which the Owner role implies) brings it along. The
        tenant Owner ROLE is what keeps the two contexts apart."""
        self.assertIn(self.platform_group, self.owner.all_group_ids)
        self.assertFalse(self.Users.with_user(self.owner)._nc_actor_is_platform_admin())
        with self.assertRaises(AccessError):
            self.module.with_user(self.owner).button_immediate_install()

    def test_a_platform_group_member_who_is_not_a_system_administrator_is_not_enough(self):
        self.assertFalse(self.Users.with_user(self.platform_only)._nc_actor_is_platform_admin())
        with self.assertRaises(AccessError):
            self.module.with_user(self.platform_only)._nc_assert_module_management_allowed('x')

    # ---------------------------------------------------------------- 2. the tenant Owner
    def test_a_tenant_owner_still_cannot_bypass_platform_entitlement(self):
        self._owner_without_platform_membership()
        self.assertFalse(self.Users.with_user(self.owner)._nc_actor_is_platform_admin())
        with self.assertRaises(AccessError):
            self.module.with_user(self.owner)._nc_assert_module_management_allowed('button_immediate_install')
        with self.assertRaises(AccessError):
            self.module.with_user(self.owner).button_immediate_install()
        for field in ('allowed_module_names', 'plan_code', 'subscription_status', 'max_users'):
            value = {'allowed_module_names': 'stock,fleet,mrp', 'plan_code': 'X', 'subscription_status': 'active',
                     'max_users': 99}[field]
            with self.assertRaises(AccessError, msg=field):
                self.config.with_user(self.owner).write({field: value})
        self.assertEqual(self.config.allowed_module_names, 'stock')

    def test_the_tenant_owner_still_manages_home_visibility_and_order(self):
        self._owner_without_platform_membership()
        Apps = self.env['ncollection.launcher.app']
        Apps._nc_sync()
        row = Apps.sudo().search([], limit=1)
        row.with_user(self.owner).write({'visible': False, 'sequence': 2})
        self.assertFalse(row.visible)
        with self.assertRaises(AccessError):
            row.with_user(self.owner).write({'state': 'active'})

    def test_in_a_tenant_database_nobody_can_be_a_platform_administrator(self):
        """The platform group does not exist there: even a system administrator is only a tenant user."""
        with self._as_tenant():
            self.assertFalse(self.Users.with_user(self.platform_admin)._nc_actor_is_platform_admin())
            with self.assertRaises(AccessError):
                self.module.with_user(self.platform_admin)._nc_assert_module_management_allowed('x')

    # ---------------------------------------------------------------- 3. ordinary users
    def test_ordinary_tenant_users_remain_restricted(self):
        self.assertFalse(self.Users.with_user(self.employee)._nc_actor_is_platform_admin())
        with self.assertRaises(AccessError):
            self.module.with_user(self.employee)._nc_assert_module_management_allowed('x')
        with self.assertRaises(AccessError):
            self.config.with_user(self.employee).write({'allowed_module_names': 'stock,mrp'})

    # ---------------------------------------------------------------- 4. cross-tenant
    def test_a_platform_operation_reaches_only_its_own_plans_tenants(self):
        """Editing one plan's licence list changes that plan's tenants' effective modules and nobody else's."""
        Plan = self.env['ncollection.subscription.plan']
        plan_a = Plan.create({'name': 'Plan A', 'code': 'PLAN_A', 'allowed_module_names': 'stock'})
        plan_b = Plan.create({'name': 'Plan B', 'code': 'PLAN_B', 'allowed_module_names': 'stock'})
        tenants = {}
        for name, plan in (('ta', plan_a), ('tb', plan_b)):
            tenants[name] = self.env['ncollection.tenant'].with_user(self.env.ref('base.user_root')).create(
                {'company_name': name.upper(), 'database_name': name + 'bd', 'database_status': 'ready',
                 'status': 'active', 'plan_id': plan.id})
        before_b = tenants['tb']._nc_effective_module_list()
        plan_a.with_user(self.platform_admin).write({'allowed_module_names': 'stock,fleet'})
        self.assertIn('fleet', tenants['ta']._nc_effective_module_list())
        self.assertEqual(tenants['tb']._nc_effective_module_list(), before_b)
        self.assertNotIn('fleet', tenants['tb']._nc_effective_module_list())

    # ---------------------------------------------------------------- 5. UI and backend agree
    def test_the_tenant_application_screen_is_not_a_platform_control(self):
        """Settings > Applications only offers Home visibility/order: no install or entitlement field is editable,
        and the platform-level control (the plan's module picker) lives on the platform plan form, not here."""
        view = self.env.ref('ncollection_core.view_nc_launcher_app_list').arch
        for forbidden in ('button_install', 'button_immediate_install', 'allowed_module_names'):
            self.assertNotIn(forbidden, view)
        self.assertIn('widget="boolean_toggle"', view)
        self.assertIn('allowed_module_names', self.env.ref('ncollection_subscription.view_ncollection_subscription_plan_form').arch
                      if self.env.ref('ncollection_subscription.view_ncollection_subscription_plan_form', raise_if_not_found=False)
                      else 'allowed_module_names')
