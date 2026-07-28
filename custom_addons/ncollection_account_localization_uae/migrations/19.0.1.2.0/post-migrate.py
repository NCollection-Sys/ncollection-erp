# -*- coding: utf-8 -*-
"""P3-T09 upgrade path — enable bilingual tax invoices for existing tenants.

The bilingual-invoice enablement (``l10n_gcc_dual_language_invoice = True``) is
applied by ``res.company._nc_apply_uae_localization`` from the module's
``post_init_hook`` — which Odoo fires only on a FRESH install, never on ``-u``.
A tenant that already had this module installed before P3-T09 therefore gets the
new invoice report view (a ``data`` file, reloaded on upgrade) but NOT the flag,
silently leaving bilingual invoicing off.

This post-migration closes that gap: it flips the per-company flag on every
company in the (already-localized) tenant DB. Idempotent — the helper only
writes companies that don't already have it — and safe to run on any tenant
(db-per-tenant + UAE-only module ⇒ every company here is a UAE company). Arabic
is already active from l10n_gcc_invoice's own install hook (see the helper).
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['res.company'].search([])._nc_enable_bilingual_invoices()
