# pylint: disable=manifest-required-author
# (proprietary NCollection module — same exemption the other ncollection_* modules carry.)
{
    'name': 'NCollection Sale Documents',
    'version': '19.0.1.1.0',
    'category': 'Sales/Sales',
    'summary': 'Sales opens on Invoices; Duplicate; View / Preview that is the very PDF Print produces',
    'author': 'NCollection',
    'website': 'https://ncollection.com',
    'license': 'LGPL-3',
    # `sale` + `account` (its Invoices menu is Odoo's own customer-invoice action) + the shared mixins of
    # ncollection_core. No model, no ACL and no route of its own.
    # auto_install: every tenant that has Sales gets it with no plan/catalog entry.
    'depends': ['sale', 'account', 'ncollection_core'],
    'data': [
        'views/sale_order_views.xml',
        'views/vendor_portal.xml',
        'data/document_types.xml',
    ],
    'auto_install': True,
}
