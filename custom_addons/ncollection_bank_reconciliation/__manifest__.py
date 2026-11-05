# pylint: disable=manifest-required-author
# (C8101 wants the OCA as author; this is a proprietary NCollection module.)
{
    'name': 'NCollection Bank Reconciliation',
    'version': '19.0.1.1.0',
    'category': 'Accounting/Accounting',
    'summary': 'Bank Transactions and Bank Matching for Community: the OCA reconciliation engine, made safe '
               '(locking, no double reconciliation, audited undo), strict statement import (preview, exact journal, '
               'duplicate fingerprints) and the Transactions / To Reconcile / Statements / New / Upload journal card',
    'author': 'NCollection',
    'website': 'https://ncollection.com',
    'license': 'LGPL-3',
    'post_load': 'post_load',
    # ADOPTS the OCA engine (decision record: docs/markdown/BANK_RECONCILIATION.md):
    # account_reconcile_oca                       : the matching workspace and the reconciliation itself (AGPL-3).
    # account_statement_import_file_reconcile_oca : an imported file opens straight in that workspace (AGPL-3).
    # account_statement_import_camt               : ISO 20022 CAMT.053 (AGPL-3).
    # account_statement_import_sheet_file_xlsx    : CSV and XLSX with a column mapping (AGPL-3).
    # account_statement_import_qif                : QIF (AGPL-3).
    # OFX: our own dependency-free parser (wizard/ofx_parser.py); the OCA module has no 19.0 release (PR #924 open)
    # and needs ofxparse, absent from the image. CODA: not offered (Belgian; no 19.0 module). No bank synchronisation.
    # Every reconciliation goes through Odoo's own account.move.line.reconcile(); nothing here edits a residual.
    'depends': [
        'account',
        'mail',
        'ncollection_core',
        'ncollection_account_core',
        'account_reconcile_oca',
        'account_statement_import_file_reconcile_oca',
        'account_statement_import_camt',
        'account_statement_import_sheet_file_xlsx',
        'account_statement_import_qif',
    ],
    'data': [
        'security/ir.model.access.csv',
        'security/security.xml',
        'views/account_journal_dashboard_views.xml',
        'views/matching_views.xml',
        'wizard/account_statement_import_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'ncollection_bank_reconciliation/static/src/scss/bank_card.scss',
            'ncollection_bank_reconciliation/static/src/js/bank_drop.js',
        ],
    },
}
