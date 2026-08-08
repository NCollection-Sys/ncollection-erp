# -*- coding: utf-8 -*-
"""#347 — repair the cron service user on tenants that already installed 15.0.

TWO things, both of which an XML change alone cannot fix, for the same reason:
`data/cron_user.xml` is noupdate="1", so once the record exists an upgrade will
never rewrite it. That is correct (a tenant may add role groups to the account
and must not have them clobbered) and it is exactly why these need code.

That version's data file wrote ``<field name="password">False</field>`` with no
``eval=``. Odoo's XML loader defaults to ``type='char'``, so it stored the
literal string "False", which is truthy and therefore got hashed into a REAL
password. Any tenant that installed 19.0.1.15.0 has a working login of
``cron@ncollection.internal`` / ``False``.

The XML fix alone cannot repair them: the record is ``noupdate="1"``, so an
upgrade will not rewrite the row. This nulls it out explicitly.

Written as raw SQL rather than an ORM write because ``res.users.password`` is a
compute/inverse field — assigning '' or False through the ORM routes back into
``_set_password`` and does not reliably clear the stored hash. The column is
what authentication reads (``COALESCE(password,'')=''`` fails every verify), so
the column is what must be cleared.
"""
import logging

_logger = logging.getLogger(__name__)

_XMLID_MODULE = "ncollection_core"
_XMLID_NAME = "user_cron_service"


def migrate(cr, version):
    if not version:
        return          # fresh install: the corrected data file is already right

    cr.execute("""
        SELECT res_id FROM ir_model_data
        WHERE module = %s AND name = %s AND model = 'res.users'
    """, (_XMLID_MODULE, _XMLID_NAME))
    row = cr.fetchone()
    if not row:
        return          # user was never created here

    user_id = row[0]

    # (1) The password. 15.0 wrote `<field name="password">False</field>` with
    #     no eval=, so the loader stored the literal string and hashed it.
    cr.execute("""
        UPDATE res_users SET password = NULL
        WHERE id = %s AND password IS NOT NULL
    """, (user_id,))
    if cr.rowcount:
        _logger.warning(
            "#347: cleared the password on the cron service user (id %s). "
            "19.0.1.15.0 shipped it with a hashed literal 'False', which was a "
            "working login. It cannot authenticate now.", user_id)

    # (2) Membership of group_cron_service, which did not exist in 15.0. Without
    #     it the scheduler cannot CREATE ncollection.alert (only group_system
    #     could), so every detected anomaly is silently discarded — the fix
    #     would close the licence bypass by breaking the feature.
    cr.execute("""
        SELECT res_id FROM ir_model_data
        WHERE module = %s AND name = 'group_cron_service' AND model = 'res.groups'
    """, (_XMLID_MODULE,))
    grp = cr.fetchone()
    if not grp:
        _logger.error(
            "#347: group_cron_service is missing, so the scheduler cannot be "
            "granted alert-create rights. Anomaly detection will record nothing.")
        return
    cr.execute("""
        INSERT INTO res_groups_users_rel (gid, uid)
        VALUES (%s, %s) ON CONFLICT DO NOTHING
    """, (grp[0], user_id))
    if cr.rowcount:
        _logger.info(
            "#347: added the cron service user (id %s) to group_cron_service so "
            "it can record alerts.", user_id)

    # (3) The READ side. group_cron_service implies the business read groups via
    #     ROLE_IMPLICATIONS, but that table is applied by post_init_hook, which
    #     runs on INSTALL ONLY — an upgrading tenant would never link them, and
    #     the scheduler would be able to write alerts while reading nothing to
    #     base them on. _sync_scheduler_read_access is idempotent and skips
    #     models this database does not have, so calling it here is safe.
    #
    #     Read-only ACLs on exactly the five detector models — NOT the app-user
    #     groups a first attempt used, which a reviewer showed carry write and
    #     unlink across Sales, Stock and HR behind a "read only" comment.
    from odoo import SUPERUSER_ID, api
    from odoo.addons.ncollection_core.hooks import _sync_scheduler_read_access

    env = api.Environment(cr, SUPERUSER_ID, {})
    # RETRACT the app-user groups an earlier build of this branch linked to
    # group_cron_service. _sync_role_implications only ever ADDS, so dropping
    # the entry from ROLE_IMPLICATIONS does not unlink what a previous run
    # already granted — a tenant upgraded against the interim build would keep
    # write/create on sale.order and crm.lead and UNLINK on stock lots and
    # hr.employee, which is precisely the over-grant this fix removes.
    cr.execute("""
        DELETE FROM res_groups_implied_rel i
        USING ir_model_data d
        WHERE i.gid = d.res_id
          AND d.model = 'res.groups'
          AND d.module = %s
          AND d.name = 'group_cron_service'
    """, (_XMLID_MODULE,))
    if cr.rowcount:
        _logger.warning(
            "#347: removed %s app-user group implication(s) from "
            "group_cron_service. An interim build granted write/unlink across "
            "Sales, Stock and HR under a comment claiming read-only.",
            cr.rowcount)
        # Odoo materialises the FULL TRANSITIVE closure onto users, so the
        # memberships those implications created must go too — otherwise the
        # user keeps the rights after the link is gone. The module list covers
        # the closure, not just the three groups named directly: review walked
        # implied_ids and found sales_team/stock/hr each also pull in `product`
        # and `purchase`, which an earlier version of this list missed.
        cr.execute("""
            DELETE FROM res_groups_users_rel r
            USING ir_model_data d
            WHERE r.uid = %s
              AND r.gid = d.res_id
              AND d.model = 'res.groups'
              AND d.module IN ('sales_team', 'sale', 'crm', 'stock', 'hr',
                               'account', 'product', 'purchase')
        """, (user_id,))
        if cr.rowcount:
            _logger.warning(
                "#347: removed %s materialised app-group membership(s) from the "
                "scheduler.", cr.rowcount)
        env.invalidate_all()

    result = _sync_scheduler_read_access(env)
    _logger.info("#347: scheduler read granted=%s skipped=%s",
                 result['granted'], result['skipped'])
