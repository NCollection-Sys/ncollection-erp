# -*- coding: utf-8 -*-
"""#347 — clear the password that 19.0.1.15.0 gave the cron service user.

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

    cr.execute("""
        UPDATE res_users SET password = NULL
        WHERE id = %s AND password IS NOT NULL
    """, (row[0],))
    if cr.rowcount:
        _logger.warning(
            "#347: cleared the password on the cron service user (id %s). "
            "19.0.1.15.0 shipped it with a hashed literal 'False', which was a "
            "working login. It cannot authenticate now.", row[0])
