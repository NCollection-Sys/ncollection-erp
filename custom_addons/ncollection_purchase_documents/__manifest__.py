# pylint: disable=manifest-required-author
# (proprietary NCollection module — same exemption the other ncollection_* modules carry.)
{
    'name': 'NCollection Purchase Documents',
    'version': '19.0.1.1.0',
    'category': 'Inventory/Purchase',
    'summary': 'Purchase opens on Vendor Bills; Duplicate; Files; View / Preview = the printed document',
    'author': 'NCollection',
    'website': 'https://ncollection.com',
    'license': 'LGPL-3',
    # `purchase` + `account` (its Vendor Bills menu is Odoo's own vendor-bill action) + the shared mixins of
    # ncollection_core. No model, no ACL and no route of its own.
    # auto_install: every tenant that has Purchase gets it with no plan/catalog entry.
    'depends': ['purchase', 'account', 'ncollection_core'],
    'data': [
        'views/purchase_order_views.xml',
        'views/vendor_portal.xml',
        'data/document_types.xml',
    ],
    'auto_install': True,
}
