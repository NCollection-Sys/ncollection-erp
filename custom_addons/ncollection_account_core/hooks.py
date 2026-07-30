# -*- coding: utf-8 -*-
"""Install hook for ncollection_account_core (F1-T02)."""


def post_init_hook(env):
    """On install, codify the accounting engine baseline on every company — the
    calendar fiscal year (31 Dec). A fresh tenant (database-per-tenant) has one
    company, configured here unattended at provisioning. Idempotent + fail-soft,
    so install never fails on it. Journals come from the chart of accounts
    (Odoo / l10n); lock dates stay operator-controlled (see README)."""
    env['res.company'].search([])._nc_apply_accounting_baseline()
