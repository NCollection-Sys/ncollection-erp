# -*- coding: utf-8 -*-
"""Companion modules reach the tenants that ALREADY exist (cheque printing follows Accounting).

``ncollection_subscription`` now derives companion modules inside
``ncollection.tenant._nc_effective_module_list`` (see its ``models/companions.py``): a tenant that is entitled to
Accounting is entitled to ``ncollection_cheque_print``. A NEW tenant gets it at database creation, because provisioning
reads that one list. An EXISTING tenant would not: nothing changed in its plan, so no plan-save hook fires, and it
would stay licensed in principle but never installed, which is the exact gap #461 closed for plan edits.

So this hands the work to the SAME entry points a plan save uses, as 19.0.6.8.0 did for #467. No new engine, no new
queue, no second path:

  * ``_config_sync_enqueue()``        re-pushes the tenant's licence (its allowed list now names the companion);
  * ``_nc_enqueue_module_install()``  one job per ready tenant, deduplicated, installing its licensed set. Installing
                                      a module that is already installed is a no-op, which is why a tenant where it
                                      was installed by hand (test11) is unaffected.

ONLY tenants that actually gain a companion are touched (their effective list contains one). A tenant without
Accounting is not queued: nothing about its entitlement changed. Both calls QUEUE; the upgrade does not wait on tenant
databases. Nothing here uninstalls anything or edits a plan.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    from odoo.addons.ncollection_subscription.models.companions import COMPANION_MODULES
    tenants = env['ncollection.tenant'].search([
        ('database_status', '=', 'ready'),
        ('database_name', '!=', False),
    ])
    gaining = tenants.filtered(
        lambda t: any(name in COMPANION_MODULES for name in t._nc_effective_module_list()))
    if not gaining:
        return
    gaining._config_sync_enqueue()
    gaining._nc_enqueue_module_install()
