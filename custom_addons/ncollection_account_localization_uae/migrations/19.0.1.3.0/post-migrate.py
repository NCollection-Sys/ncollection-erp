# -*- coding: utf-8 -*-
"""P3-T06 upgrade path — apply the AED multi-currency setup to existing tenants.

The currency setup (`res.company._nc_setup_uae_currencies`) runs from
`_nc_apply_uae_localization` in the module's `post_init_hook` — which Odoo fires
only on a FRESH install, never on `-u`. A tenant that already had this module
installed before P3-T06 would therefore miss the GCC currency activation,
multi-currency group, peg rates and AED rounding on upgrade.

This post-migration closes that gap. The helper is idempotent (skips currencies
already rated for the company) and fail-soft, and safe on any tenant DB
(db-per-tenant + UAE-only module ⇒ every company here is a UAE company).
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['res.company'].search([])._nc_setup_uae_currencies()
