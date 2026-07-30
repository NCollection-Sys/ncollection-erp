# -*- coding: utf-8 -*-
"""P3-T01 — backfill the Enterprise plan's interim OCA financial module set.

The Enterprise plan gained ``allowed_module_names`` (account +
account_financial_report + ncollection_mis_templates) so a provisioned Enterprise
tenant gets the interim financial reports (Trial Balance / GL / BS-P&L). The plan
records live in a ``noupdate="1"`` data file (demo_data.xml), so a FRESH install
picks up the new value but an EXISTING platform DB would not — this backfills it.

Only sets the field when it is still empty, so an admin who has customised the
plan's module set is never clobbered. Fail-soft is unnecessary here (a plain
ORM write on the platform DB); a genuine failure should surface loudly.
"""
from odoo import SUPERUSER_ID, api

_ENTERPRISE_MODULES = "account,account_financial_report,ncollection_mis_templates"


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    plan = env["ncollection.subscription.plan"].search(
        [("code", "=", "ENTERPRISE")], limit=1)
    if plan and not (plan.allowed_module_names or "").strip():
        plan.allowed_module_names = _ENTERPRISE_MODULES
