"""Discount types still on the old default (taxes split over the lines) follow the company's Calculation Rules.

Identical bookings under the default rule (before tax); they only differ if the company later chooses after tax.
A type with explicit taxes (tax_mode fixed) is not touched."""


def migrate(cr, version):
    cr.execute("UPDATE ncollection_invoice_adjustment_type SET tax_mode = 'company' "
               "WHERE kind = 'discount' AND tax_mode = 'lines'")
