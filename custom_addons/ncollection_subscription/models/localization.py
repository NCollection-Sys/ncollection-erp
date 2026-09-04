# -*- coding: utf-8 -*-
"""Country localization packages (#469).

WHAT A PACKAGE IS. A country's answer to "what does a tenant in this country
need before it can do accounting at all": which modules must be installed at
database creation, which chart template the company must end up on, and which
currency. That is all. It is a lookup table, not a framework — adding Saudi
Arabia or Egypt is one entry here plus its l10n module, with no change to the
provisioning engine.

WHY IT LIVES IN ncollection_subscription. This module owns ``ncollection.tenant``
and the plan module picker, and ncollection_saas (provisioning, config sync,
module install) depends on it. Putting the table here gives all four consumers
ONE source of truth rather than the copy-with-a-pinning-test arrangement
CORE_TENANT_MODULES needs — that duplication exists only because the picker
cannot import the SaaS layer, and this table has no such problem.

WHY THE MODULES ARE NOT PLAN-SELECTABLE. ``l10n_ae`` is not a feature an
operator buys; it is what makes the tenant's books legal. Offering it in the
picker would let someone deselect a live tenant's chart of accounts, or select
it for a tenant whose country is something else — a setup that cannot be
repaired by unticking the box, because a chart template cannot be un-loaded.
``PLAN_EXCLUDED_MODULES`` below is what keeps them out; the tenant is still
LICENSED for them, because the licensed set is the plan's modules UNION the
tenant's package (``ncollection.tenant._nc_effective_module_list``), so Ring 1
shows the localization menus without anyone choosing a technical module.

WHY THE MODULES MUST BE INSTALLED AT CREATION AND NOT LATER. Odoo's ``account``
install schedules a deferred ``try_loading('generic_coa')``. A tenant that gets
its l10n module afterwards has already taken that fallback — USD, placeholder
15% taxes — and loading the real chart then means loading it OVER existing
accounting data, which is the one case that is genuinely unsafe. Installing the
package in the SAME ``-i`` as ``account`` means the localization post_init runs
while the database is still empty, and it cancels the pending generic fallback.
Measured on a live tenant before this ticket: chart ``generic_coa``, currency
USD, country US, with ``ncollection_account_localization_uae`` installed.
"""

# code -> package. Keys are ISO 3166-1 alpha-2, matching res.country.code.
LOCALIZATION_PACKAGES = {
    'AE': {
        'name': 'United Arab Emirates',
        # Installed at database creation, in this order, alongside the plan's
        # modules. `l10n_ae` is Odoo's official UAE localization (chart, 5%/0%/
        # exempt taxes, tax groups, fiscal positions); `base_vat` is the VAT
        # number check `ncollection_account_localization_uae` extends with the
        # TRN rule; the NCollection module applies the chart and seeds the FTA
        # checklist on top.
        'modules': ('base_vat', 'l10n_ae', 'ncollection_account_localization_uae'),
        # What the company must ACTUALLY be on afterwards. This is the
        # verification target, not a hint: provisioning fails the job if the
        # tenant did not end up here (`l10n_ae is installed` is not evidence).
        'chart_template': 'ae',
        'currency': 'AED',
    },
}

# Every module named by any package. The plan module picker refuses to offer
# these — see the module docstring.
PLAN_EXCLUDED_MODULES = tuple(sorted({
    module
    for package in LOCALIZATION_PACKAGES.values()
    for module in package['modules']
}))


def localization_package(country_code):
    """The package for an ISO country code, or None.

    Returns None for an unknown or empty code rather than raising: a tenant in
    a country we have no package for is a normal, supported state — it simply
    provisions with no localization, exactly as every tenant did before #469.
    """
    if not country_code:
        return None
    return LOCALIZATION_PACKAGES.get(country_code.upper())


def localization_selection():
    """Selection values for the supported countries, for a UI that wants them."""
    return [(code, package['name'])
            for code, package in sorted(LOCALIZATION_PACKAGES.items())]
