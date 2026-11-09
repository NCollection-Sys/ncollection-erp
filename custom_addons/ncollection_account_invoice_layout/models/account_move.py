# -*- coding: utf-8 -*-
"""Invoice header: the exact percentage for a fixed-amount discount, and the discount summary above Untaxed Amount.

TOTAL DISCOUNT  = sum over the product lines of (undiscounted tax-excluded line total - booked Amount).
TOTAL AFTER DISCOUNT = Untaxed Amount minus the invoice-level charges (see invoice adjustments). With no charge it equals
Untaxed Amount; both are derived from the booked lines, so they cannot contradict Untaxed Amount, Taxes and Total.
"""
from odoo import api, fields, models


class AccountMove(models.Model):
    _inherit = 'account.move'

    nc_total_discount = fields.Monetary(
        string='Total Discount', currency_field='currency_id', compute='_compute_nc_discount_totals',
        help="All line discounts, tax-excluded: what the lines would total without any discount, minus what they total.")
    nc_total_after_discount = fields.Monetary(
        string='Total After Discount', currency_field='currency_id', compute='_compute_nc_discount_totals',
        help="The discounted, tax-excluded total of the product lines, before tax and before invoice-level charges.")
    nc_has_discount = fields.Boolean(compute='_compute_nc_discount_totals')

    def _prepare_product_base_line_for_taxes_computation(self, product_line):
        """A fixed-amount discount feeds the tax engine its EXACT percentage so the subtotal is gross - fixed to the cent."""
        base_line = super()._prepare_product_base_line_for_taxes_computation(product_line)
        if self.is_invoice(include_receipts=True) and product_line.nc_discount_mode == 'amount':
            gross = product_line.quantity * product_line.price_unit
            base_line['discount'] = product_line.nc_discount_fixed / gross * 100.0 if gross else 0.0
        return base_line

    @api.depends('invoice_line_ids.price_subtotal', 'invoice_line_ids.discount', 'invoice_line_ids.nc_discount_fixed',
                 'invoice_line_ids.nc_discount_mode', 'invoice_line_ids.quantity', 'invoice_line_ids.price_unit',
                 'invoice_line_ids.tax_ids', 'amount_untaxed')
    def _compute_nc_discount_totals(self):
        tax_engine = self.env['account.tax']
        for move in self:
            discount = 0.0
            if move.is_invoice(include_receipts=True):
                company = move.company_id or self.env.company
                for line in move.invoice_line_ids.filtered(lambda l: l.display_type == 'product'):
                    base_line = move._prepare_product_base_line_for_taxes_computation(line)
                    base_line['discount'] = 0.0
                    tax_engine._add_tax_details_in_base_line(base_line, company)
                    undiscounted = base_line['tax_details']['raw_total_excluded_currency']
                    discount += move.currency_id.round(undiscounted) - line.price_subtotal
            discount = move.currency_id.round(discount) if move.currency_id else discount
            move.nc_total_discount = discount
            move.nc_total_after_discount = (move.amount_untaxed or 0.0)
            move.nc_has_discount = bool(move.currency_id and not move.currency_id.is_zero(discount))
