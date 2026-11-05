# pylint: disable=manifest-required-author
{
    'name': 'NCollection Bank Reconciliation - Sales Orders',
    'version': '19.0.1.0.0',
    'category': 'Accounting/Accounting',
    'summary': "The Sales action of the bank matching workspace: find the customer's confirmed sales orders, create "
               "the invoices with Odoo's own invoicing (regular or down payment) and match them with the transaction",
    'author': 'NCollection',
    'website': 'https://ncollection.com',
    'license': 'LGPL-3',
    # A bridge: only meaningful when both the bank workspace and Sales are installed, so it is never a plan module.
    'depends': ['ncollection_bank_reconciliation', 'sale'],
    'data': [
        'security/ir.model.access.csv',
        'views/sale_match_views.xml',
    ],
    'auto_install': True,
}
