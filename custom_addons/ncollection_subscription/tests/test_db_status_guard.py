# -*- coding: utf-8 -*-
"""ISO-1 defense-in-depth (#228): the database_status transition guard.

A group_platform_admin has write access to ncollection.tenant. This proves it
CANNOT flip database_status to 'ready'/'provisioning' by hand — only the
provisioning engine (which sets the ``nc_provisioning`` context) or a superuser
may. The takeover itself is already closed by #225's unique(database_name)
constraint; this is a cleaner boundary on top.
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
        # a real platform-admin user: HAS write on ncollection.tenant, but is NOT
        # the superuser — the exact actor the guard must stop.
        cls.admin_user = cls.env['res.users'].create({
            'login': 'guard_platform_admin', 'name': 'Guard PA',
            'group_ids': [(6, 0, [
                cls.env.ref('base.group_user').id,
                cls.env.ref('ncollection_subscription.group_platform_admin').id,
            ])]})

    def test_platform_admin_cannot_set_engine_only_statuses(self):
        tenant = self.tenant.with_user(self.admin_user)
        for status in ('ready', 'provisioning'):
            with self.assertRaises(ValidationError):
                tenant.write({'database_status': status})

    def test_platform_admin_may_set_unguarded_statuses(self):
        # 'error' is not engine-only, so a platform admin can still set it.
        tenant = self.tenant.with_user(self.admin_user)
        tenant.write({'database_status': 'error'})
        self.assertEqual(tenant.database_status, 'error')

    def test_engine_context_may_set_ready(self):
        # the provisioning engine authorises the transition via the context key.
        self.tenant.with_context(nc_provisioning=True).write(
            {'database_status': 'ready'})
        self.assertEqual(self.tenant.database_status, 'ready')

    def test_superuser_may_set_ready(self):
        # the test env runs as SUPERUSER_ID — a plain write still works, proving
        # the many existing fixtures that seed 'ready' tenants are unaffected.
        self.tenant.write({'database_status': 'ready'})
        self.assertEqual(self.tenant.database_status, 'ready')
