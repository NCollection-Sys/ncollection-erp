# -*- coding: utf-8 -*-
"""Odoo-19 _sql_constraints sweep — ncollection.domain FQDN uniqueness.

The old `_sql_constraints` list was silently ignored by Odoo 19, so unique(fqdn)
was never enforced. It is now declared via models.Constraint. Fail loud +
actionable if duplicate FQDNs already exist before ADD CONSTRAINT UNIQUE runs.
"""


def migrate(cr, version):
    cr.execute("SELECT to_regclass('ncollection_domain')")
    if cr.fetchone()[0] is None:
        return
    cr.execute("""
        SELECT fqdn, array_agg(id ORDER BY id)
        FROM ncollection_domain
        WHERE fqdn IS NOT NULL
        GROUP BY fqdn
        HAVING count(*) > 1
    """)
    dupes = cr.fetchall()
    if dupes:
        detail = "; ".join("fqdn %s -> ids %s" % (fqdn, ids) for fqdn, ids in dupes)
        raise Exception(  # pylint: disable=broad-exception-raised
            "Odoo-19 sql-constraint sweep: cannot add UNIQUE(fqdn) on "
            "ncollection_domain — duplicate FQDNs exist; reconcile them before "
            "upgrading: %s" % detail)
