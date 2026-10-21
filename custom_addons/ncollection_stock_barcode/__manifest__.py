# -*- coding: utf-8 -*-
{
    'name': 'NCollection Stock Barcode',
    'version': '19.0.1.1.1',
    'category': 'Inventory/Inventory',
    'summary': 'Barcode scanning workflow for receipts, deliveries, transfers and inventory counts, '
               'on Odoo Community stock (hardware scanner or camera).',
    'description': """
Warehouse barcode scanning, built on what Odoo Community already has (product / location / lot barcodes, the
barcode scanner service and the camera scanner). The scanning APP (stock_barcode) is Enterprise and the OCA port is not
on 19.0, so this module is the thin workflow in between:

* Receipts, deliveries and internal transfers: scan the operation, scan products (and lots / serials, and the
  location), correct a mis-scan, validate with the stock engine's own validation (its backorder wizard included).
* Inventory count: scan a location, scan what is there, apply with the engine's own inventory adjustment.

It never replaces a stock flow: a scan changes the same fields a person would change by hand, under the user's own
rights, in the user's own company. See models/stock_barcode.py.
""",
    'author': 'NCollection',
    'website': 'https://ncollection.com',
    'license': 'LGPL-3',
    # stock: the engine this drives. barcodes: the scanner service. ncollection_branding: the shared card design
    # system (the screen is built from it, with no card CSS of its own). ncollection_core: the Home launcher and the
    # plan licensing its tile follows (ir_ui_menu NATIVE_FOLLOWS_LICENCE). Pure tenant layer: never queried from the
    # platform layer (Standing Rule 3).
    'depends': ['stock', 'barcodes', 'ncollection_core', 'ncollection_branding'],
    'data': [
        'views/barcode_menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'ncollection_stock_barcode/static/src/scanner/scanner.scss',
            'ncollection_stock_barcode/static/src/scanner/scanner.js',
            'ncollection_stock_barcode/static/src/scanner/scanner.xml',
        ],
    },
    # A tenant that has Inventory gets scanning; the entitlement stays the platform's (it is stock that is licensed).
    'auto_install': ['stock', 'ncollection_branding'],
    'application': False,
    'installable': True,
}
