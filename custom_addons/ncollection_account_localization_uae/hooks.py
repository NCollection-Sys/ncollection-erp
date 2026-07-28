# -*- coding: utf-8 -*-
"""Install hook for ncollection_account_localization_uae (F5-T01 + P3-T04)."""


def post_init_hook(env):
    """On install, for every company: apply the UAE VAT localization (P3-T04 —
    loads Odoo's 'ae' chart template if the company has no CoA yet) and seed the
    standard FTA compliance checklist (F5-T01).

    A fresh tenant (database-per-tenant) has exactly one company, set up here
    unattended during provisioning. Both steps are idempotent + fail-soft, so
    install never fails on them. (The res.company.create override seeds the FTA
    checklist for any later-created company; the chart-template load stays an
    explicit install/onboarding action, not an every-create side effect.)"""
    companies = env['res.company'].search([])
    companies._nc_apply_uae_localization()
    Item = env['ncollection.fta.compliance.item']
    for company in companies:
        Item._ensure_for_company(company)
