# -*- coding: utf-8 -*-
"""Record rules scope a reseller to its OWN records at the ORM (P10-T09).

The highest-value check: a reseller must never see another reseller's tenants or
account — enforced at the ORM (record rules), not merely hidden in the UI
(Rule 4/7). We read as each reseller's own user, not as the test admin.
"""
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestResellerIsolation(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        group_reseller = cls.env.ref('ncollection_reseller.group_reseller')
        group_user = cls.env.ref('base.group_user')

        def user(login):
            # Odoo 19: the res.users groups relation is group_ids (not groups_id).
            return cls.env['res.users'].create({
                'name': login, 'login': login, 'email': '%s@t.test' % login,
                'group_ids': [(6, 0, [group_user.id, group_reseller.id])],
            })

        cls.user_a = user('reseller_a')
        cls.user_b = user('reseller_b')
        pa = cls.env['res.partner'].create({'name': 'A'})  # arch-guard: admin-db-platform
        pb = cls.env['res.partner'].create({'name': 'B'})  # arch-guard: admin-db-platform
        cls.res_a = cls.env['ncollection.reseller'].create({
            'name': 'A', 'partner_id': pa.id, 'user_id': cls.user_a.id})
        cls.res_b = cls.env['ncollection.reseller'].create({
            'name': 'B', 'partner_id': pb.id, 'user_id': cls.user_b.id})
        cls.tenant_a = cls.env['ncollection.tenant'].create({
            'company_name': 'A tenant', 'database_name': 'atenant',
            'reseller_id': cls.res_a.id})
        cls.tenant_b = cls.env['ncollection.tenant'].create({
            'company_name': 'B tenant', 'database_name': 'btenant',
            'reseller_id': cls.res_b.id})

    def test_reseller_sees_only_own_tenants(self):
        seen = self.env['ncollection.tenant'].with_user(self.user_a).search([])
        self.assertIn(self.tenant_a, seen)
        self.assertNotIn(self.tenant_b, seen)

    def test_reseller_sees_only_own_account(self):
        seen = self.env['ncollection.reseller'].with_user(self.user_a).search([])
        self.assertEqual(seen, self.res_a)

    def test_reseller_cannot_provision_under_other(self):
        # ORM ownership gate: reseller A's user cannot provision a sub-tenant
        # (and burn quota / bill) under reseller B, even by passing B's record.
        plan = self.env['ncollection.subscription.plan'].create({
            'name': 'IsoP', 'code': 'ISO-P', 'monthly_price': 10.0})
        with self.assertRaises(AccessError):
            self.res_b.with_user(self.user_a).provision_subtenant(
                company_name='Evil', subdomain='eviliso', plan=plan)

    def test_reseller_cannot_read_other_account(self):
        # Record rule filters reseller B out of any read done as reseller A.
        self.assertFalse(
            self.env['ncollection.reseller'].with_user(self.user_a).search(
                [('id', '=', self.res_b.id)]))
