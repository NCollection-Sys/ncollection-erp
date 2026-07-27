# -*- coding: utf-8 -*-
"""Install hook for ncollection_account_localization_uae (F5-T01)."""


def post_init_hook(env):
    """Seed the standard UAE FTA compliance items for every EXISTING company
    (idempotent). New companies get theirs via the res.company.create override,
    so between the two every tenant company always has its checklist."""
    Item = env['ncollection.fta.compliance.item']
    for company in env['res.company'].search([]):
        Item._ensure_for_company(company)
