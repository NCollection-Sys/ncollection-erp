# pylint: disable=manifest-required-author
# (proprietary NCollection module — same exemption the other ncollection_* modules carry.)
{
    'name': 'NCollection Sale Attachments',
    'version': '19.0.1.0.0',
    'category': 'Sales/Sales',
    'summary': 'See and browse the attachments of a quotation / sales order straight from the list view',
    'author': 'NCollection',
    'website': 'https://ncollection.com',
    'license': 'LGPL-3',
    # `sale` + the shared Files mixin, paperclip widget and viewer of ncollection_core (Odoo's own FileViewer,
    # extended, not copied). Attachments are Odoo's own ir.attachment rows and access is the record's access: no
    # model, no ACL and no route of our own. auto_install: every tenant that has Sales gets it with no plan entry.
    'depends': ['sale', 'ncollection_core'],
    'data': [
        'views/sale_order_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': True,
}
