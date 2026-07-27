# -*- coding: utf-8 -*-
"""Odoo-19 _sql_constraints sweep — subscription plan code uniqueness ENFORCED.

unique(code) on ncollection.subscription.plan used to be declared via the old
`_sql_constraints` list, which Odoo 19 silently ignores — so it was never created
in the DB (verified: the live table had only its pkey). This proves the
models.Constraint replacement is enforced.

(ncollection.module's technical_name constraint is deliberately NOT swept here —
that model is dead code: models/__init__.py never imports it, so it is not in the
registry and its views are not in the manifest. Flagged for a separate follow-up.)
"""
from psycopg2 import IntegrityError

from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger


@tagged('post_install', '-at_install')
class TestSubscriptionSqlConstraints(TransactionCase):

    def test_plan_code_unique_enforced(self):
        Plan = self.env['ncollection.subscription.plan']
        Plan.create({'name': 'Plan A', 'code': 'DUP_SC_CODE'})
        with mute_logger('odoo.sql_db'), self.assertRaises(IntegrityError):
            with self.env.cr.savepoint():
                Plan.create({'name': 'Plan B', 'code': 'DUP_SC_CODE'})
                self.env.flush_all()
