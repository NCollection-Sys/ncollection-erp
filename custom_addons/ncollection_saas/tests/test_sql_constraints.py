# -*- coding: utf-8 -*-
"""Odoo-19 _sql_constraints sweep — ncollection.domain FQDN uniqueness ENFORCED.

The old `_sql_constraints` list was silently ignored by Odoo 19, so unique(fqdn)
was never created — two tenant records could claim the same custom domain. This
proves the models.Constraint replacement enforces it at the DB level.
"""
from psycopg2 import IntegrityError

from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger


@tagged('post_install', '-at_install')
class TestDomainSqlConstraint(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['ir.config_parameter'].sudo().set_param(
            'ncollection_saas.provisioning_quota_per_hour', '0')
        plan = cls.env['ncollection.subscription.plan'].create({
            'name': 'Dom Plan', 'code': 'DOM_SC_PLAN'})
        cls.tenant = cls.env['ncollection.tenant'].create({
            'company_name': 'Dom Co', 'database_name': 'domscco',
            'plan_id': plan.id, 'database_status': 'ready'})

    def test_fqdn_unique_enforced(self):
        Domain = self.env['ncollection.domain']
        Domain.create({'tenant_id': self.tenant.id, 'fqdn': 'dup.example.com'})
        with mute_logger('odoo.sql_db'), self.assertRaises(IntegrityError):
            with self.env.cr.savepoint():
                Domain.create({'tenant_id': self.tenant.id, 'fqdn': 'dup.example.com'})
                self.env.flush_all()
