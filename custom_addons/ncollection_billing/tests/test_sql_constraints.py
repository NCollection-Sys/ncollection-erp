# -*- coding: utf-8 -*-
"""Odoo-19 _sql_constraints sweep — billing idempotency constraint is ENFORCED.

Odoo 19 silently ignores the old `_sql_constraints` list, so
unique(nc_subscription_id, nc_period_start) on account.move was never created —
i.e. nothing at the DB level stopped a subscription being invoiced twice for the
same period. This proves the models.Constraint replacement actually enforces it.
"""
from psycopg2 import IntegrityError

from odoo import fields
from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger


@tagged('post_install', '-at_install')
class TestBillingSqlConstraint(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        plan = cls.env['ncollection.subscription.plan'].create({
            'name': 'SC Plan', 'code': 'SC_BILL_PLAN', 'monthly_price': 100.0})
        tenant = cls.env['ncollection.tenant'].create({
            'company_name': 'SC Co', 'database_name': 'scbillco',
            'email': 'owner@scbillco.example', 'plan_id': plan.id,
            'status': 'trial', 'database_status': 'ready'})
        today = fields.Date.context_today(cls.env.user)
        cls.sub = cls.env['ncollection.subscription'].create({
            'tenant_id': tenant.id, 'plan_id': plan.id, 'billing_cycle': 'monthly',
            'status': 'draft', 'start_date': today,
            'end_date': fields.Date.add(today, days=30)})

    def test_billing_period_unique_enforced(self):
        vals = {'move_type': 'entry', 'nc_subscription_id': self.sub.id,
                'nc_period_start': '2026-01-01'}
        self.env['account.move'].create(vals)  # arch-guard: admin-db-billing
        with mute_logger('odoo.sql_db'), self.assertRaises(IntegrityError):
            with self.env.cr.savepoint():
                self.env['account.move'].create(dict(vals))  # arch-guard: admin-db-billing
                self.env.flush_all()
