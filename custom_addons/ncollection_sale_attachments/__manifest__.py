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
    # `sale` only. Attachments are Odoo's own ir.attachment rows, the viewer is Odoo's own FileViewer
    # (extended, not copied), and access is the record's access: no model, no ACL and no route of our own.
    # auto_install: every tenant that has Sales gets it with no plan/catalog entry.
    'depends': ['sale'],
    'data': [
        'views/sale_order_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'ncollection_sale_attachments/static/src/viewer/nc_file_viewer.scss',
            'ncollection_sale_attachments/static/src/viewer/nc_file_viewer.js',
            'ncollection_sale_attachments/static/src/viewer/nc_file_viewer.xml',
            'ncollection_sale_attachments/static/src/widget/attachment_count_field.js',
            'ncollection_sale_attachments/static/src/widget/attachment_count_field.xml',
            'ncollection_sale_attachments/static/src/widget/attachment_count_field.scss',
        ],
    },
    'installable': True,
    'application': False,
    'auto_install': True,
}
