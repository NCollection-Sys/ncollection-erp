# pylint: disable=manifest-required-author
# (C8101 wants the OCA as author; this is a proprietary NCollection module.)
{
    'name': 'NCollection Bank & Cash Journal Operations',
    'version': '19.0.1.1.0',
    'category': 'Accounting/Accounting',
    'summary': 'Bank and cash journal cards as an operations centre: ledger Current Balance, Pending Balance, '
               'complete Transactions, Payment / Receipt (Customer, Supplier or Account), Internal Transfer, '
               'Reconcile and Mark as Paid. Every bank / cash journal, existing or new.',
    'author': 'NCollection',
    'website': 'https://ncollection.com',
    'license': 'LGPL-3',
    # No engine of our own: payments are Odoo's account.payment, settlements Odoo statement lines, matches Odoo's
    # reconcile(). Design: docs/markdown/JOURNAL_OPERATIONS.md.
    'depends': [
        'account',
        'analytic',
        'ncollection_account_core',
        'ncollection_bank_reconciliation',
    ],
    'data': [
        'security/ir.model.access.csv',
        'security/security.xml',
        'data/sequence.xml',
        'views/journal_transaction_views.xml',
        'views/account_payment_views.xml',
        'views/internal_transfer_views.xml',
        'views/payment_settle_views.xml',
        'views/account_journal_dashboard_views.xml',
        'views/terminology.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'ncollection_journal_operations/static/src/scss/journal_card.scss',
        ],
    },
    'auto_install': True,
}
