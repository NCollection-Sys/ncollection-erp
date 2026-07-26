# -*- coding: utf-8 -*-
"""ISO-1 (#225) pre-migration — runs BEFORE the new UNIQUE(database_name) applies.

Two steps:
  1. Clear any INVALID database_name (underscores, path chars, over-length — not
     matching ^[a-z][a-z0-9]{2,62}$) from tenants that have NO live database yet
     (not_provisioned / error / unset). Such a name is unroutable cruft;
     `_ensure_database_name` regenerates a valid, collision-free one at
     provisioning. Provisioned tenants (provisioning/ready) are left untouched —
     their name is a live database we must not orphan.
  2. Fail LOUD + actionable (Standing Rule 10) if any tenants still SHARE a
     database_name, so the UNIQUE constraint doesn't abort the upgrade later with
     a raw UniqueViolation and no clue which rows to reconcile.

Forward-only and idempotent (a second run matches nothing / finds no dupes).
"""
import logging

_logger = logging.getLogger(__name__)

_VALID_NAME = "^[a-z][a-z0-9]{2,62}$"


def migrate(cr, version):
    cr.execute(
        """
        UPDATE ncollection_tenant
           SET database_name = NULL
         WHERE database_name IS NOT NULL
           AND database_name !~ %s
           AND (database_status IS NULL
                OR database_status NOT IN ('provisioning', 'ready'))
        """,
        (_VALID_NAME,),
    )
    if cr.rowcount:
        _logger.warning(
            "ISO-1 #225: nulled %s invalid database_name(s) on unprovisioned "
            "tenants (they will be regenerated on provisioning).", cr.rowcount)

    # Fail LOUD + actionable (Standing Rule 10) if any remaining tenants still
    # SHARE a database_name — the new UNIQUE(database_name) would otherwise abort
    # the upgrade in _auto_init with a raw UniqueViolation and no clue which rows.
    # (Provisioning de-collides names, so this should never fire — but a hand-
    # edited/imported DB could, and ops must reconcile before the fix can deploy.)
    cr.execute(
        """
        SELECT database_name, array_agg(id ORDER BY id)
          FROM ncollection_tenant
         WHERE database_name IS NOT NULL
         GROUP BY database_name HAVING count(*) > 1
        """
    )
    dupes = cr.fetchall()
    if dupes:
        detail = "; ".join("%r -> tenant ids %s" % (name, ids) for name, ids in dupes)
        raise Exception(  # pylint: disable=broad-exception-raised
            "ISO-1 #225: cannot add UNIQUE(database_name) — these live tenants "
            "share a database name; reconcile them before upgrading: %s" % detail)
