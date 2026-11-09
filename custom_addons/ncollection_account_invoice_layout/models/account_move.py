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
                 'invoice_line_ids.tax_ids', 'invoice_line_ids.nc_adjustment_id', 'amount_untaxed')
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
            charges = invoice_discounts = 0.0
            for line in move.invoice_line_ids.filtered(lambda l: l.display_type == 'product' and l.nc_adjustment_id):
                if line.nc_adjustment_id.kind == 'discount':
                    invoice_discounts += -line.price_subtotal
                else:
                    charges += line.price_subtotal
            discount = move.currency_id.round(discount + invoice_discounts) if move.currency_id else discount + invoice_discounts
            move.nc_total_discount = discount
            move.nc_total_after_discount = move.currency_id.round((move.amount_untaxed or 0.0) - charges) if move.currency_id else (move.amount_untaxed or 0.0) - charges
            move.nc_has_discount = bool(move.currency_id and not move.currency_id.is_zero(discount))

    # ------------------------------------------------------------------ invoice-level charges and discounts
    nc_adjustment_ids = fields.One2many('ncollection.invoice.adjustment', 'move_id', string='Charges & Adjustments', copy=False)

    @api.model_create_multi
    def create(self, vals_list):
        moves = super().create(vals_list)
        moves._nc_add_default_adjustments()
        moves._nc_sync_adjustments()
        return moves

    def write(self, vals):
        result = super().write(vals)
        if not self.env.context.get('nc_adjustment_sync') and (
                {'invoice_line_ids', 'line_ids', 'currency_id', 'invoice_date'} & set(vals)):
            self._nc_sync_adjustments()
        return result

    def _nc_add_default_adjustments(self):
        Type = self.env['ncollection.invoice.adjustment.type']
        Adjustment = self.env['ncollection.invoice.adjustment'].with_context(nc_adjustment_sync=True)
        for move in self.filtered(lambda m: m.state == 'draft' and m.is_invoice(include_receipts=True)):
            existing = move.nc_adjustment_ids.type_id
            for type_ in Type.search([('company_id', '=', move.company_id.id), ('auto_add', '=', True)]):
                if type_ not in existing and type_._applies_to_move(move):
                    Adjustment.create({'move_id': move.id, 'type_id': type_.id, 'value': type_.default_value,
                                       'tax_ids': [(6, 0, type_.tax_ids.ids)],
                                       'analytic_distribution': type_.analytic_distribution})

    def _nc_adjustment_basis(self):
        """P: the discounted, tax-excluded product lines that are NOT adjustment lines."""
        self.ensure_one()
        return sum(self.invoice_line_ids.filtered(
            lambda l: l.display_type == 'product' and not l.nc_adjustment_id).mapped('price_subtotal'))

    def _nc_sync_adjustments(self):
        """Book every adjustment of a draft invoice as a real invoice line, in the documented calculation order."""
        if self.env.context.get('nc_adjustment_sync'):
            return
        for move in self.filtered(lambda m: m.state == 'draft' and m.is_invoice(include_receipts=True)):
            adjustments = move.nc_adjustment_ids.sorted(lambda a: (a.sequence, a.id))
            if not adjustments and not move.invoice_line_ids.filtered('nc_adjustment_id'):
                continue
            currency = move.currency_id or move.company_id.currency_id
            basis = currency.round(move._nc_adjustment_basis())
            amounts = {}
            discount_total = 0.0
            for adj in adjustments.filtered(lambda a: a.kind == 'discount'):
                amount = adj.value if adj.value_mode == 'fixed' else basis * adj.value / 100.0
                amounts[adj.id] = currency.round(min(amount, max(basis - discount_total, 0.0)))
                discount_total += amounts[adj.id]
            for adj in adjustments.filtered(lambda a: a.kind == 'charge'):
                if adj.value_mode == 'fixed':
                    amount = adj.value
                else:
                    base = basis - discount_total if adj.type_id.basis == 'lines_after_discount' else basis
                    amount = base * adj.value / 100.0
                amounts[adj.id] = currency.round(amount)
            sync = move.with_context(nc_adjustment_sync=True, check_move_validity=False)
            for adj in adjustments:
                amount = amounts[adj.id]
                sign = -1.0 if adj.kind == 'discount' else 1.0
                adj.with_context(nc_adjustment_sync=True).write({'amount': amount})
                line_vals = {
                    'name': adj.type_id.name, 'quantity': 1.0, 'price_unit': sign * amount, 'discount': 0.0,
                    'nc_discount_mode': 'percent', 'account_id': adj.type_id.account_id.id, 'tax_ids': [(6, 0, adj.tax_ids.ids)],
                    'analytic_distribution': adj.analytic_distribution or adj.type_id.analytic_distribution,
                    'nc_adjustment_id': adj.id, 'sequence': 9000 + adj.sequence}
                line = adj.line_id.exists()
                if currency.is_zero(amount):
                    if line:
                        line.with_context(nc_adjustment_sync=True).unlink()
                    continue
                if line:
                    line.with_context(nc_adjustment_sync=True, check_move_validity=False).write(line_vals)
                else:
                    line = self.env['account.move.line'].with_context(nc_adjustment_sync=True, check_move_validity=False).create(
                        dict(line_vals, move_id=move.id, display_type='product'))
                    adj.with_context(nc_adjustment_sync=True).write({'line_id': line.id})
            # a line whose adjustment is gone must not stay booked
            orphan = sync.invoice_line_ids.filtered(lambda l: l.nc_adjustment_id and l.nc_adjustment_id not in adjustments)
            if orphan:
                orphan.with_context(nc_adjustment_sync=True).unlink()

    def unlink(self):
        # deleting a draft invoice deletes its booked adjustment lines with it
        return super(AccountMove, self.with_context(nc_adjustment_sync=True)).unlink()
