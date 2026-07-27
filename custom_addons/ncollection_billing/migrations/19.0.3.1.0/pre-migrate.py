# -*- coding: utf-8 -*-
"""Odoo-19 _sql_constraints sweep — billing idempotency backstop.

The old `_sql_constraints` list on account.move was silently ignored by Odoo 19
(orm/model_classes.py logs "no longer supported"), so the unique index that
enforces "one invoice per subscription per billing period" was never created.
This module now declares it via models.Constraint. Before ADD CONSTRAINT UNIQUE
runs, fail loud + actionable if any duplicate subscription-period invoices
already exist — otherwise the upgrade would abort with a cryptic Postgres error.
"""


def migrate(cr, version):
    cr.execute("SELECT to_regclass('account_move')")
    if cr.fetchone()[0] is None:
        return
    cr.execute("""
        SELECT nc_subscription_id, nc_period_start, array_agg(id ORDER BY id)
        FROM account_move
        WHERE nc_subscription_id IS NOT NULL AND nc_period_start IS NOT NULL
        GROUP BY nc_subscription_id, nc_period_start
        HAVING count(*) > 1
    """)
    dupes = cr.fetchall()
    if dupes:
        detail = "; ".join(
            "subscription %s / period %s -> account.move ids %s" % (sub, period, ids)
            for sub, period, ids in dupes)
        raise Exception(  # pylint: disable=broad-exception-raised
            "Odoo-19 sql-constraint sweep: cannot add "
            "UNIQUE(nc_subscription_id, nc_period_start) on account_move — these "
            "subscription-period invoices are already duplicated (a billing "
            "idempotency breach). Cancel/delete the duplicates before upgrading: "
            "%s" % detail)
