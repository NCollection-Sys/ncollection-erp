# -*- coding: utf-8 -*-
"""Gated modules: ELIGIBLE by a fact about the tenant, ACTIVATED only by an explicit per-tenant decision.

THE PROBLEM. UAE e-invoicing must be available to UAE tenants and to nobody else, yet switched on only for the
specific tenants someone selected. Neither existing mechanism fits:

  * a COMPANION (companions.py) is derived automatically from its host and has no switch of its own, so every tenant
    with Accounting would receive it, UAE or not;
  * a LOCALIZATION PACKAGE (localization.py) is automatic per country and installs at database creation;
  * naming the module in a PLAN activates it for every tenant on that plan.

THE SHAPE. A gated module is entitled by exactly one route, the existing per-tenant ``enable`` override (#476), and
only while the tenant is ELIGIBLE. Eligibility is a computed answer, never stored as an entitlement:

    eligible  =  the tenant's country is the rule's country
             AND the tenant's books are VERIFIED to be that country's (the accounting fact, below)
             AND the tenant's entitlement already includes every module in ``requires``

THE ACCOUNTING FACT, not the label. The registry's ``country_id`` is what someone typed when the tenant was created;
it has been wrong (a tenant registered as AE whose company is on the US chart in USD). The answer therefore comes from
``ncollection.tenant._nc_books_verified()``: the recorded result of the tenant-side check that proved the company is
on the country package's chart and currency. The platform layer cannot read the tenant's company directly (Rule 3), so
that verified record is the platform's only truthful source. It defaults to False here (fail-closed) and is provided
by ncollection_saas, which owns the verification.

WHAT THIS IS NOT. Not a second provisioning system: it adds no install path, only a filter on the one authority
(``_nc_effective_module_list``) and a refusal at the one place a per-tenant decision is recorded (the override).
There is no bulk action, no cron and no "all tenants of a country". Losing eligibility REVOKES the licence (the menus
disappear) and never uninstalls anything, the same rule every other revocation follows.
"""

GATED_MODULES = {
    'ncollection_uae_einvoicing': {
        'country': 'AE',
        'requires': ('account',),
        'label': 'UAE e-invoicing',
    },
    # an ASP adapter: optional, never in a plan, and only for a tenant that holds (and is eligible for) the engine
    'ncollection_uae_einvoicing_acube': {
        'country': 'AE',
        'requires': ('ncollection_uae_einvoicing',),
        'label': 'UAE e-invoicing: A-Cube adapter',
    },
}


def gated_rule(module_name):
    """The gate for ``module_name``, or None when it is not gated."""
    return GATED_MODULES.get(module_name)


def is_gated(module_name):
    return module_name in GATED_MODULES
