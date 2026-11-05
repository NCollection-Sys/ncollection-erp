# pylint: disable=manifest-required-author
# (C8101 wants the OCA as author; this is a proprietary NCollection module.)
{
    'name': 'NCollection Bank Reconciliation',
    'version': '19.0.1.0.0',
    'category': 'Accounting/Accounting',
    'summary': 'Bank Transactions and Bank Matching for Community: the OCA reconciliation engine, made safe '
               '(locking, no double reconciliation, audited undo), strict statement import (preview, exact journal, '
               'duplicate fingerprints) and the Transactions / To Reconcile / Statements / New / Upload journal card',
    'author': 'NCollection',
    'website': 'https://ncollection.com',
    'license': 'LGPL-3',
    # ADOPTS the OCA engine (decision record: docs/markdown/BANK_RECONCILIATION.md):
    # account_reconcile_oca                       : the matching workspace and the reconciliation itself (AGPL-3).
    # account_statement_import_file_reconcile_oca : an imported file opens straight in that workspace (AGPL-3).
    # account_statement_import_camt               : ISO 20022 CAMT.053 (AGPL-3).
    # account_statement_import_sheet_file_xlsx    : CSV and XLSX with a column mapping (AGPL-3).
    # account_statement_import_qif                : QIF (AGPL-3).
    # OFX is NOT offered: there is no 19.0 OCA module for it (verified 2026-11-05). No bank synchronisation.
    # Every reconciliation goes through Odoo's own account.move.line.reconcile(); nothing here edits a residual.
    'depends': [
        'account',
        'ncollection_core',
        'ncollection_account_core',
        'account_reconcile_oca',
        'account_statement_import_file_reconcile_oca',
        'account_statement_import_camt',
        'account_statement_import_sheet_file_xlsx',
        'account_statement_import_qif',
    ],
    'data': [
        'views/account_journal_dashboard_views.xml',
        'wizard/account_statement_import_views.xml',
    ],
    'assets': {
        'web.assets_backend': ['ncollection_bank_reconciliation/static/src/scss/bank_card.scss'],
    },
}
