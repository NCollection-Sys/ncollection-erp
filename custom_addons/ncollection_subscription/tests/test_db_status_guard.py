# -*- coding: utf-8 -*-
"""ISO-1 defense-in-depth (#228): the database_status transition guard.

Only a superuser (env.su / SUPERUSER_ID) may move a tenant into
'provisioning'/'ready'. The provisioning engine authorises its own transitions
with sudo() — NOT a context key, because env.context is client-controlled over
RPC and a context flag would be trivially forgeable by the very actor this
guards (a group_platform_admin with write access). The takeover itself is
already closed by #225's unique(database_name) constraint; this is a cleaner
boundary on top.
"""
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestDbStatusGuard(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.plan = cls.env['ncollection.subscription.plan'].create({
            'name': 'Guard Plan', 'code': 'GUARDPLAN'})
        cls.tenant = cls.env['ncollection.tenant'].create({
            'company_name': 'Guard Co', 'database_name': 'guardco',
            'plan_id': cls.plan.id})
        # a real platform-admin: HAS write on ncollection.tenant, is NOT the
        # superuser — the exact actor the guard must stop.
        cls.admin_user = cls.env['res.users'].create({
            'login': 'guard_platform_admin', 'name': 'Guard PA',
            'group_ids': [(6, 0, [
                cls.env.ref('base.group_user').id,
                cls.env.ref('ncollection_subscription.group_platform_admin').id,
            ])]})
        # a base.group_system admin: HAS create on ncollection.tenant but is NOT
        # the superuser either (the create-path actor).
        cls.system_user = cls.env['res.users'].create({
            'login': 'guard_sysadmin', 'name': 'Guard Sys',
            'group_ids': [(6, 0, [cls.env.ref('base.group_system').id])]})

    def test_platform_admin_cannot_set_engine_only_statuses(self):
        tenant = self.tenant.with_user(self.admin_user)
        for status in ('ready', 'provisioning'):
            with self.assertRaises(ValidationError):
                tenant.write({'database_status': status})

    def test_context_key_does_not_bypass_guard(self):
        # THE security regression (#228 review): env.context is client-controlled
        # over RPC, so a forged 'nc_provisioning' context must NOT authorise the
        # transition. A platform admin setting it themselves stays blocked.
        tenant = self.tenant.with_user(self.admin_user).with_context(
            nc_provisioning=True)
        with self.assertRaises(ValidationError):
            tenant.write({'database_status': 'ready'})

    def test_platform_admin_may_set_unguarded_statuses(self):
        # 'error' is not engine-only, so a platform admin can still set it.
        tenant = self.tenant.with_user(self.admin_user)
        tenant.write({'database_status': 'error'})
        self.assertEqual(tenant.database_status, 'error')

    def test_sudo_transition_allowed_even_from_a_non_super_base(self):
        # the engine's mechanism: sudo() elevates env.su regardless of the user
        # the job runs as (OCA queue_job replays as the enqueuing user), so the
        # guarded transition succeeds — and sudo() is NOT RPC-spoofable.
        self.tenant.with_user(self.admin_user).sudo().write(
            {'database_status': 'ready'})
        self.assertEqual(self.tenant.database_status, 'ready')

    def test_superuser_may_set_ready(self):
        # the test env runs as SUPERUSER_ID — a plain write still works, proving
        # the many existing fixtures that seed 'ready' tenants are unaffected.
        self.tenant.write({'database_status': 'ready'})
        self.assertEqual(self.tenant.database_status, 'ready')

    def test_system_admin_cannot_create_ready_tenant(self):
        # create() is guarded too: base.group_system has create rights on the
        # model but is not the superuser, so it cannot materialise a 'ready'
        # tenant that never ran the engine.
        with self.assertRaises(ValidationError):
            self.env['ncollection.tenant'].with_user(self.system_user).create({
                'company_name': 'Sneaky Co', 'database_name': 'sneakyco',
                'plan_id': self.plan.id, 'database_status': 'ready'})
