# -*- coding: utf-8 -*-
{
    'name': 'NCollection UAE Localization',
    'version': '19.0.1.0.0',
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
    # NOTE: l10n_ae (Odoo's UAE CoA/tax templates, in the image) is deliberately
    # NOT a dependency here — the "mechanisms stay Odoo-owned" data/templates get
    # wired in by #44/#45 (oca-scout survey on #126), keeping this scaffold's
    # footprint matched to its actual deliverables.
    'depends': ['account', 'ncollection_account_core', 'base_vat'],
    'data': [
        'security/ir.model.access.csv',
        'security/fta_compliance_security.xml',
        'views/fta_compliance_views.xml',
    ],
    # Seeds the standard FTA items for existing companies; new companies get
    # theirs via res.company.create.
    'post_init_hook': 'post_init_hook',
}
