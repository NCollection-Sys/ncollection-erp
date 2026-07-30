# -*- coding: utf-8 -*-
{
    'name': 'NCollection Account Core',
    'version': '19.0.2.0.0',
    'category': 'Accounting/Accounting',
    'summary': 'Shared financial base: SaaS config surface, subscription '
               'restriction hooks, common mixins and the accounting engine '
               'baseline + boundary guard for the ncollection_account_* family',
    'author': 'NCollection',
    'website': 'https://ncollection.com',
    'license': 'LGPL-3',
    # F1-T01 (FINANCIAL_PLATFORM_ARCHITECTURE.md §7): the thin base layer between
    # Odoo's `account` engine and the rest of the financial platform. It depends
    # on:
    #   - account         : the Odoo accounting engine it extends (never
    #                       replaces — Odoo owns posting/journals/taxes).
    #   - ncollection_core : home of the tenant-side license machinery (#11) —
    #                       ncollection.workspace.config + the Ring-2 ORM
    #                       enforcer. The subscription-restriction mixin READS
    #                       that config (never a second enforcement path), so it
    #                       needs core installed. core ships in every tenant
    #                       (CORE_TENANT_MODULES), so this adds no new footprint.
    'depends': ['account', 'ncollection_core'],
    # F1-T02: codify the fiscal-year baseline (31 Dec) on the tenant company at
    # provisioning via a post_init hook (hooks.py -> res.company). It only sets
    # Odoo's own native fields — no fiscal-year model, no posting/tax logic
    # (FPA §4/§6). The engine-boundary guard lives in tests/test_engine_boundary.
    'post_init_hook': 'post_init_hook',
    # Scaffold: the load-bearing deliverable is the shared AbstractModel mixin
    # (models/account_mixin.py). It is abstract (no records) and there is no
    # concrete model yet, so there is deliberately no security/ir.model.access
    # or view data. The "SaaS Configuration / Configuration UI" responsibility
    # (FPA §7) is acknowledged and DEFERRED to the first downstream consumer that
    # needs a real setting — see README.rst.
    # installable/application/auto_install are intentionally omitted: each would
    # only repeat an Odoo default (True / False / False).
}
