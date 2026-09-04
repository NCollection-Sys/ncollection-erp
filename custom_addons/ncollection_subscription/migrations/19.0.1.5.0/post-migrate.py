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

Idempotent: a second run adds nothing and writes nothing.

THIS WRITE DOES NOT FAN OUT, and that is not a choice made here. A plan write
normally queues a config sync and a module install for every ready tenant, via
the `write()` override in ncollection_saas/models/config_sync.py — but
ncollection_saas DEPENDS ON this module, so this post-migrate runs BEFORE that
`_inherit` is merged into the model, and the write goes through the plain
`write()`. Measured: upgrading the platform DB produced zero new queue jobs.

Licensing a module without installing it is exactly the defect #461 fixed, so
the fan-out is done by ncollection_saas's own 19.0.6.8.0 migration, which runs
later and therefore has the full model. Keep the two in step: this file owns
the DATA, that one owns reaching the tenants.

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
