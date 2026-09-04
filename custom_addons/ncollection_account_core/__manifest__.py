# -*- coding: utf-8 -*-
{
    'name': 'NCollection Account Core',
    'version': '19.0.1.2.0',
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
    'data': [
        # #474: the single financial application, named "Accounting".
        'views/accounting_app_menus.xml',
    ],
    'post_init_hook': 'post_init_hook',
    # Scaffold: the load-bearing deliverable is the shared AbstractModel mixin
    # (models/account_mixin.py). It is abstract (no records) and there is no
    # concrete model yet, so there is deliberately no security/ir.model.access
    # or view data. The "SaaS Configuration / Configuration UI" responsibility
    # (FPA §7) is acknowledged and DEFERRED to the first downstream consumer that
    # needs a real setting — see README.rst.
    # DEPENDENCY-ONLY, deliberately (#467). Every ncollection_account_* sibling
    # gained 'application': True so a SaaS operator can license it from the plan
    # module picker; this one does NOT. It ships no menu, no action and no
    # concrete model, so offering it as a plan choice would present a checkbox
    # that changes nothing an operator can see — while every module that needs
    # it already pulls it in as a dependency, and Ring 1 expands the dependency
    # closure at read time, so licensing a sibling licenses this too.
    # installable/application/auto_install stay omitted: each would only repeat
    # an Odoo default (True / False / False).
}
