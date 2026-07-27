# -*- coding: utf-8 -*-
"""Odoo-19 _sql_constraints sweep — subscription plan code uniqueness.

The old `_sql_constraints` list was silently ignored by Odoo 19, so unique(code)
on ncollection.subscription.plan was never enforced. It is now declared via
models.Constraint. Fail loud + actionable if duplicate plan codes already exist
before the unique index is created.
"""


def migrate(cr, version):
    cr.execute("SELECT to_regclass('ncollection_subscription_plan')")
    if cr.fetchone()[0] is None:
        return
    cr.execute("""
        SELECT code, array_agg(id ORDER BY id)
        FROM ncollection_subscription_plan
        WHERE code IS NOT NULL
        GROUP BY code HAVING count(*) > 1
    """)
    dupes = cr.fetchall()
    if dupes:
        detail = "; ".join("plan code %s -> ids %s" % (code, ids) for code, ids in dupes)
        raise Exception(  # pylint: disable=broad-exception-raised
            "Odoo-19 sql-constraint sweep: cannot add UNIQUE(code) on "
            "ncollection_subscription_plan — duplicate plan codes exist; "
            "reconcile them before upgrading: %s" % detail)
