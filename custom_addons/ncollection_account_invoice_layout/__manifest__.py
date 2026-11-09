# pylint: disable=manifest-required-author
# (proprietary NCollection module — same exemption the other ncollection_* modules carry.)
{
    'name': 'NCollection Invoice Layout & Calculations',
    'version': '19.0.1.0.0',
    'category': 'Accounting',
    'summary': 'Invoice line columns, fixed / percentage discounts, editable Amount and Total, '
               'invoice-level charges and discounts, discount totals',
    'author': 'NCollection',
    'website': 'https://ncollection.com',
    'license': 'LGPL-3',
    # Design record and worked examples: docs/markdown/INVOICE_LAYOUT_AND_CALCULATIONS.md.
    # Every figure comes from Odoo's own tax engine (account.tax._add_tax_details_in_base_line), never from a second formula.
    'depends': ['account', 'ncollection_account_core'],
    'post_init_hook': 'post_init_hook',
    'data': [
        'security/ir.model.access.csv',
        'security/rules.xml',
        'views/account_move_views.xml',
        'views/adjustment_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'ncollection_account_invoice_layout/static/src/invoice_lines.scss',
        ],
    },
}
