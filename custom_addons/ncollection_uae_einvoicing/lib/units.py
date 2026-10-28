# -*- coding: utf-8 -*-
"""Odoo unit of measure → UN/ECE Recommendation 20 code (PINT-AE CL-23).

Explicit and closed. Odoo core's own e-invoicing helper silently maps every unit it does not know to ``C62`` ("one");
here an unknown or ambiguous unit (a custom one, 'Pack of 6') maps to nothing and the document is blocked with
``NCOLLECTION:unit-code-unmapped`` until the unit is mapped on purpose. Every target code is asserted to exist in the
shipped
official list by the tests."""

# uom.uom external id -> UN/ECE Rec 20
UOM_XMLID_TO_UNECE = {
    'uom.product_uom_unit': 'C62',
    'uom.product_uom_dozen': 'DZN',
    'uom.product_uom_kgm': 'KGM',
    'uom.product_uom_gram': 'GRM',
    'uom.product_uom_ton': 'TNE',
    'uom.product_uom_lb': 'LBR',
    'uom.product_uom_oz': 'ONZ',
    'uom.product_uom_litre': 'LTR',
    'uom.product_uom_milliliter': 'MLT',
    'uom.product_uom_gal': 'GLL',
    'uom.product_uom_qt': 'QTL',
    'uom.product_uom_floz': 'OZA',
    'uom.product_uom_meter': 'MTR',
    'uom.product_uom_cm': 'CMT',
    'uom.product_uom_millimeter': 'MMT',
    'uom.product_uom_km': 'KMT',
    'uom.product_uom_inch': 'INH',
    'uom.product_uom_foot': 'FOT',
    'uom.product_uom_yard': 'YRD',
    'uom.product_uom_mile': 'SMI',
    'uom.product_uom_square_meter': 'MTK',
    'uom.product_uom_square_foot': 'FTK',
    'uom.product_uom_cubic_meter': 'MTQ',
    'uom.product_uom_cubic_foot': 'FTQ',
    'uom.product_uom_cubic_inch': 'INQ',
    'uom.product_uom_hour': 'HUR',
    'uom.product_uom_day': 'DAY',
    'uom.product_uom_minute': 'MIN',
    'uom.product_uom_kwh': 'KWH',
}


def unit_code(external_id):
    """The Rec 20 code for an Odoo unit's external id, or None when unmapped."""
    return UOM_XMLID_TO_UNECE.get(external_id)
