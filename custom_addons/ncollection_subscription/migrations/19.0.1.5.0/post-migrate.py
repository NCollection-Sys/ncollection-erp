# -*- coding: utf-8 -*-
"""#467 — license the NATIVE financial stack on the Enterprise plan.

Enterprise previously named only Odoo/OCA modules (``account``,
``account_financial_report``, ``ncollection_mis_templates``), because no
``ncollection_account_*`` module declared ``application`` and the plan module
picker filters on exactly that — so not one native accounting module could be
chosen, and every one of them sat ``uninstalled`` in every tenant database.

The plan records live in a ``noupdate="1"`` data file (demo_data.xml), so a
FRESH install picks the new value up but an EXISTING platform DB would not.

ADDITIVE, not a replacement: the new names are UNIONed onto whatever the plan
currently has, so an admin who has customised the set keeps every one of their
choices and the interim OCA reports keep working until #117 retires them. Order
is preserved (existing names first, then the additions in declaration order) so
the stored string does not churn.

Idempotent: a second run adds nothing and writes nothing, which matters because
a plan write fans out a config sync AND a module-install job to every ready
tenant on the plan (ncollection_saas/models/config_sync.py).

ncollection_account_assets is deliberately absent — see demo_data.xml.
"""
from odoo import SUPERUSER_ID, api

_NATIVE_FINANCIAL_MODULES = (
    'ncollection_account_reports',
    'ncollection_account_dashboard',
    'ncollection_account_budget',
    'ncollection_account_localization_uae',
)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    plan = env['ncollection.subscription.plan'].search(
        [('code', '=', 'ENTERPRISE')], limit=1)
    if not plan:
        return
    current = plan.get_allowed_module_list()
    missing = [name for name in _NATIVE_FINANCIAL_MODULES if name not in current]
    if not missing:
        return
    plan.allowed_module_names = ','.join(current + missing)
