# pylint: disable=manifest-required-author
# (C8101 wants the OCA as author; this is a proprietary NCollection module.)
{
    'name': 'NCollection Account Assets',
    'version': '19.0.1.0.0',
    'category': 'Accounting/Accounting',
    'summary': 'Fixed-asset registration, depreciation, disposal, transfer '
               'and the Asset Register report (F4-T03)',
    'author': 'NCollection',
    'website': 'https://ncollection.com',
    'license': 'LGPL-3',
    # ADOPT + wrap (oca-scout, #122). Unlike #120 and #121, which both
    # concluded BUILD-CUSTOM, the OCA engine is a genuine fit here:
    #
    #   - Odoo 19 Community ships NO fixed assets at all. `account_asset` moved
    #     to Enterprise; verified against the running image (no account_asset
    #     directory, no `account.asset` model in any shipped addon). So Rule 2
    #     ("extend before replacing") has no core to attach to.
    #   - OCA account_asset_management (19.0, "Mature") owns the part that is
    #     expensive AND dangerous to reinvent: depreciation boards (linear,
    #     linear-limit, degressive, degressive-linear, degressive-limit), the
    #     posting of each period through a real account.move, and disposal with
    #     plus-/min-value recognition.
    #   - Decisively, it inherits `analytic.mixin` on BOTH account.asset and
    #     account.asset.profile, i.e. it carries `analytic_distribution` — the
    #     same dimension mechanism #120 established. The single-FK analytic
    #     model that disqualified account_budget_oca in #121 does NOT reproduce
    #     here, which is why the two tickets reach opposite conclusions.
    #   - FPA §4 puts accounting-engine logic on Odoo's side of the line
    #     ("Odoo owns accounting; NCollection owns business experience"), and a
    #     depreciation schedule posting double-entry is engine logic.
    #
    # What OCA does NOT provide, and this module therefore owns:
    #   (a) the ACCOUNT-TYPE GUARD. The engine's three profile accounts are
    #       unconstrained Many2one(account.account). #114's cash flow treats
    #       `expense_depreciation` as a non-cash add-back moved OUT of
    #       Investing, and #411's BS/P&L maps place it likewise. A profile
    #       pointed at a plain `expense` account keeps the cash flow BALANCED
    #       while attributing the money to the wrong section — silently. See
    #       models/asset_profile.py.
    #   (b) a TRANSFER flow. OCA models registration, depreciation and removal;
    #       "transfer" is not one of its concepts, and FPA §7 lists it.
    #   (c) the ASSET REGISTER on the F2 engine. OCA ships its own XLSX report
    #       and wizard; FPA reserves reporting for ncollection_account_reports,
    #       and the acceptance criterion says "via F2 engine" explicitly.
    #
    # LICENSE NOTE. account_asset_management is AGPL-3. This wrapper declares
    # LGPL-3, following the precedent already in the tree —
    # ncollection_mis_templates is LGPL-3 and depends on AGPL-3 mis_builder.
    # That precedent is inherited, not independently settled; recorded in
    # docs/markdown/OCA_DEPENDENCIES.md rather than decided here.
    'depends': [
        'account',
        # DECLARED, not inherited: wizard/asset_transfer.py inherits
        # `analytic.mixin` directly. It arrives transitively today through
        # account_asset_management -> account -> analytic, which works right up
        # until that chain changes — and then fails at install with a bare
        # KeyError. Same reasoning ncollection_account_budget applied to `mail`.
        'analytic',
        # The OCA fixed-asset engine (repos.yml: ./oca/account-financial-tools).
        'account_asset_management',
        'ncollection_account_core',
        # The F2 report engine the acceptance criterion names.
        'ncollection_account_reports',
    ],
    'data': [
        'security/ir.model.access.csv',
        'security/asset_security.xml',
        'views/asset_register_views.xml',
        'views/asset_transfer_views.xml',
    ],
}
