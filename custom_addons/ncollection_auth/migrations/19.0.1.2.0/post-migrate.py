# -*- coding: utf-8 -*-
"""Bring existing tenants into the two-stage policy (#261).

TWO things in data/ do not reach an upgraded tenant, both for the same reason,
and neither is optional:

  * the new `log_skeleton_days` parameter (a <function>), and
  * the cron's `code`, which must now run BOTH stages (a <record>).

#261 splits auth-log retention into MINIMISE (log_retention_days, 180) then
DELETE (log_skeleton_days, 400). The new default lives in data/auth_params.xml
inside ``<data noupdate="1">`` — and Odoo runs a ``<function>`` in such a block
ONLY at install:

    # odoo/tools/convert.py
    def _tag_function(self, rec):
        if self.noupdate and self.mode != 'init':
            return

So `-u` never writes the row on an already-installed tenant. That is correct
and deliberate for the block as a whole (it is what stops an upgrade clobbering
per-tenant tuning of the other keys), but it means the new key needs a
migration or it simply never appears.

Falling back to the default in code is not sufficient, and this is the case
that makes it a real bug rather than a tidiness point: a tenant that had raised
log_retention_days above 400 — the module's own docstring anticipates "3,650
for a ten-year mandate" — would get skeleton(400) < retention(3650), which the
coherence guard REFUSES. _gc_auth_log would then raise on every single cron
run, and Odoo deactivates a cron after 5 failures across 7 days. The delete
stage would switch itself off, silently, on exactly the tenants that care most
about retention.

So: write the key if it is absent, at max(default, retention + the 30-day
floor) so the result is coherent by construction — a real skeleton window, not
a zero-day one. Never overwrite an existing value — an operator who
has already tuned it outranks this script.
"""


def migrate(cr, version):
    cr.execute("SELECT to_regclass('ir_config_parameter')")
    if cr.fetchone()[0] is None:
        return
    _upgrade_cron_code(cr)

    cr.execute(
        "SELECT value FROM ir_config_parameter WHERE key = %s",
        ('ncollection_auth.log_skeleton_days',))
    if cr.fetchone():
        return                      # already set — the operator's value wins

    cr.execute(
        "SELECT value FROM ir_config_parameter WHERE key = %s",
        ('ncollection_auth.log_retention_days',))
    row = cr.fetchone()
    try:
        retention = int(str(row[0]).strip()) if row else 180
    except (TypeError, ValueError):
        # Unparseable retention is its own loud failure at runtime
        # (_retention_days raises). Do not compound it here; seed the plain
        # default and let that surface normally.
        retention = 180

    # max(default, retention + FLOOR) — not max(default, retention). The latter
    # yields skeleton == retention for any retention >= 400, i.e. a zero-day
    # skeleton, which the coherence rule refuses as "a flat delete wearing two
    # stages". Caught by test_an_upgraded_tenant_with_a_long_retention_still_purges.
    skeleton = max(400, retention + 30)
    cr.execute(
        "INSERT INTO ir_config_parameter (key, value, create_uid, create_date, "
        "write_uid, write_date) VALUES (%s, %s, 1, now(), 1, now())",
        ('ncollection_auth.log_skeleton_days', str(skeleton)))


def _upgrade_cron_code(cr):
    """Point the existing cron at BOTH stages. See the module docstring."""
    cr.execute("""
        SELECT s.id, s.code
        FROM ir_act_server s
        JOIN ir_model_data d
          ON d.model = 'ir.actions.server' AND d.res_id = s.id
        WHERE d.module = 'ncollection_auth'
    """)
    for action_id, code in cr.fetchall():
        if code and '_gc_auth_log' in code and '_minimise_auth_log' not in code:
            cr.execute(
                "UPDATE ir_act_server SET code = %s WHERE id = %s",
                ("try:\n"
                 "    model._minimise_auth_log()\n"
                 "except Exception:\n"
                 "    log(\"auth-log minimisation failed; running the purge "
                 "anyway\", level=\"error\")\n"
                 "model._gc_auth_log()", action_id))
