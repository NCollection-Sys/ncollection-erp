# -*- coding: utf-8 -*-
{
    'name': 'NCollection UAE Localization',
    'version': '19.0.1.3.0',
    'category': 'Accounting/Localizations',
    'summary': 'UAE localization scaffold: TRN validation + FTA compliance '
               'tracking; the home for the NCollection UAE VAT/CoA/invoice work',
    'author': 'NCollection',
    'website': 'https://ncollection.com',
    'license': 'LGPL-3',
    # F5-T01 (FINANCIAL_PLATFORM_ARCHITECTURE.md §7). The UAE localization
    # module home. This ticket ships only the SCAFFOLD slice — TRN validation +
    # the FTA compliance checklist — and is the module that #44 (VAT config),
    # #45 (CoA), #46 (AED/currency) and #49 (bilingual invoices) land inside.
    #   - account                    : the Odoo accounting engine (never replaced).
    #   - ncollection_account_core   : the financial family base. Its
    #                                  ncollection.account.mixin (feature gating)
    #                                  is not consumed by this scaffold yet — the
    #                                  gated VAT/CoA logic that uses it lands with
    #                                  #44/#45 inside this module; the dependency
    #                                  is declared now to keep the account_* graph
    #                                  consistent (mirrors the family layering).
    #   - base_vat                   : Odoo's VAT-check dispatch; we add check_vat_ae
    #                                  to it (extend, don't replace) for the UAE TRN.
    #   - l10n_ae                     : Odoo's official UAE localization — the 'ae'
    #                                  chart template (5%/0%/exempt VAT, tax groups,
    #                                  fiscal positions, CoA) and the bilingual
    #                                  l10n_gcc tax-invoice document. P3-T04 REUSES
    #                                  this Odoo-owned mechanism and applies it to
    #                                  the tenant company at install (hooks.py /
    #                                  res_company._nc_apply_uae_localization).
    #   - ncollection_branding       : provides res.company.nc_primary_color, which
    #                                  the UAE invoice brand accent renders
    #                                  (views/report_invoice.xml, P3-T09). Declared
    #                                  explicitly because we reference the field
    #                                  directly — don't lean on the transitive graph.
    'depends': [
        'account', 'ncollection_account_core', 'ncollection_branding',
        'base_vat', 'l10n_ae',
    ],
    'data': [
        'security/ir.model.access.csv',
        'security/fta_compliance_security.xml',
        'views/fta_compliance_views.xml',
        'views/report_invoice.xml',
    ],
    # Seeds the standard FTA items for existing companies; new companies get
    # theirs via res.company.create.
    'post_init_hook': 'post_init_hook',
}
