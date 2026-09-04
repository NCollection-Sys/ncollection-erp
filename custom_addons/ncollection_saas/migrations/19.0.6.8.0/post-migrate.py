# -*- coding: utf-8 -*-
"""#467 — make the ENTERPRISE backfill actually REACH existing tenants.

ncollection_subscription's 19.0.1.5.0 migration adds the native financial
modules to the ENTERPRISE plan. That write would normally fan out — the plan
`write()` override in ncollection_saas/models/config_sync.py queues a config
sync AND a module install for every ready tenant on the plan (#461).

IT DOES NOT FIRE THERE, and the reason is module load order rather than
anything about the write. ncollection_saas DEPENDS ON ncollection_subscription,
so the subscription module is loaded — and its post-migrate runs — BEFORE the
saas `_inherit` that carries the override is merged into the model. The
migration therefore writes through the plain `write()`.

Measured, not assumed: upgrading the platform DB produced zero new queue jobs,
and the tenant only received the modules once the plan was written again at
runtime.

Left alone, that reproduces the exact defect #461 fixed — a module licensed in
the plan and visible in the launcher while never existing in the tenant's
database. So this migration, which runs in the LATER module and therefore has
the full model, hands the work to the SAME existing entry point the plan-save
path uses. No new engine, no new queue, no second code path:

  * `_nc_enqueue_module_install()` — one job per ready tenant, deduplicated by
    identity_key, installing that tenant's licensed set. Installing a module
    that is already installed is a no-op, so a tenant that needs nothing is
    unaffected.
  * `_config_sync_enqueue()` — re-pushes licensing, healing any tenant whose
    config sync was missed while the override was inert.

Both are queued, not executed: the upgrade does not wait on tenant databases.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    tenants = env['ncollection.tenant'].search([
        ('database_status', '=', 'ready'),
        ('database_name', '!=', False),
    ])
    if not tenants:
        return
    tenants._config_sync_enqueue()
    tenants._nc_enqueue_module_install()
