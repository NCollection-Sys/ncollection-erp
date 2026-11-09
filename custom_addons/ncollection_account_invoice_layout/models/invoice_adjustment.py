# -*- coding: utf-8 -*-
"""Invoice-level charges and discounts (freight, a global discount, any other configured adjustment).

ARCHITECTURE. An adjustment is DATA, never code: the administrator configures ``ncollection.invoice.adjustment.type``
records (name, charge or discount, fixed or percentage, basis, account, taxes, analytic distribution, applicability).
An invoice carries ``ncollection.invoice.adjustment`` rows (type + the user's value). Each row with a non-zero amount is
booked as a REAL invoice line (quantity 1, price = +amount for a charge, -amount for a discount, the type's account, taxes
and analytic distribution), so the journal entry, the taxes, the receivable / payable and every reconciliation are
Odoo's own. Nothing edits ``amount_total``; there is no expression language and nothing is evaluated.

CALCULATION ORDER (also in docs/markdown/INVOICE_LAYOUT_AND_CALCULATIONS.md)
  1. line discounts (Disc.% / fixed) -> P = sum of the discounted, tax-excluded PRODUCT lines. Adjustment lines are never
     part of any basis, so an adjustment cannot feed itself or another one's percentage.
  2. discounts: fixed amount, or percentage of P.  G = their total.
  3. charges:   fixed amount, or percentage of P (basis "lines") or of P - G (basis "lines after discount").
  4. taxes: each adjustment line carries the taxes of its type (editable per invoice), computed by Odoo's engine.
  Untaxed Amount = P - G + F (F = charges). Total Discount = line discounts + G. Total After Discount = P - G.

Only DRAFT invoices change; a posted invoice keeps the lines it was posted with.
"""
from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

KINDS = [('charge', 'Charge'), ('discount', 'Discount')]


class InvoiceAdjustmentType(models.Model):
    _name = 'ncollection.invoice.adjustment.type'
    _description = 'Invoice charge / discount definition'
    _inherit = ['analytic.mixin']
    _order = 'sequence, id'

    name = fields.Char(string='Label', required=True, translate=True)
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company, index=True)
    kind = fields.Selection(KINDS, required=True, default='charge')
    value_mode = fields.Selection([('fixed', 'Fixed amount'), ('percent', 'Percentage')], string='Value', required=True,
                                  default='fixed')
    default_value = fields.Float(string='Default value', digits=(16, 4))
    basis = fields.Selection(
        [('lines', 'Invoice lines (after line discounts)'),
         ('lines_after_discount', 'Invoice lines after the invoice discounts')],
        string='Percentage of', default='lines', required=True,
        help="Only for a percentage. Discounts always use the first basis; a charge may use the second.")
    applies_to = fields.Selection(
        [('both', 'Customer invoices and vendor bills'), ('customer', 'Customer invoices only'),
         ('vendor', 'Vendor bills only')], default='both', required=True)
    auto_add = fields.Boolean(
        string='On every new invoice', help="A new invoice starts with this adjustment, ready for its value.")
    account_id = fields.Many2one(
        'account.account', required=True, check_company=True,
        domain="[('account_type', 'not in', ('asset_receivable', 'liability_payable', 'off_balance'))]")
    tax_ids = fields.Many2many('account.tax', string='Taxes', check_company=True,
                               help="The taxes of the booked line. A VAT-able freight charge carries the sales VAT.")

    @api.constrains('value_mode', 'basis', 'kind', 'default_value')
    def _check_type(self):
        for rec in self:
            if rec.default_value < 0:
                raise ValidationError(self.env._("The default value of \"%s\" cannot be negative.", rec.name))
            if rec.value_mode == 'percent' and rec.default_value > 100:
                raise ValidationError(self.env._("A percentage of \"%s\" cannot exceed 100.", rec.name))
            if rec.kind == 'discount' and rec.basis != 'lines':
                raise ValidationError(self.env._("A discount is always calculated on the invoice lines."))

    def _applies_to_move(self, move):
        self.ensure_one()
        if self.company_id != move.company_id:
            return False
        sale = move.is_sale_document(include_receipts=True)
        return self.applies_to == 'both' or (self.applies_to == 'customer') == sale

    def write(self, vals):
        result = super().write(vals)
        if {'account_id', 'tax_ids', 'kind', 'name'} & set(vals):
            drafts = self.env['ncollection.invoice.adjustment'].search([
                ('type_id', 'in', self.ids), ('move_id.state', '=', 'draft')])
            drafts.move_id._nc_sync_adjustments()
        return result


class InvoiceAdjustment(models.Model):
    _name = 'ncollection.invoice.adjustment'
    _description = 'Invoice charge / discount'
    _inherit = ['analytic.mixin']
    _order = 'sequence, id'

    move_id = fields.Many2one('account.move', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='move_id.company_id', store=True, index=True)
    currency_id = fields.Many2one(related='move_id.currency_id')
    type_id = fields.Many2one('ncollection.invoice.adjustment.type', string='Adjustment', required=True, ondelete='restrict',
                              domain="[('company_id', '=', company_id)]")
    kind = fields.Selection(related='type_id.kind', store=True)
    value_mode = fields.Selection(related='type_id.value_mode')
    sequence = fields.Integer(related='type_id.sequence', store=True)
    value = fields.Float(digits=(16, 4), help="An amount, or a percentage when the adjustment is a percentage.")
    tax_ids = fields.Many2many('account.tax', string='Taxes', check_company=True)
    amount = fields.Monetary(currency_field='currency_id', readonly=True,
                             help="The money booked, tax-excluded and always positive; a discount is booked negative.")
    line_id = fields.Many2one('account.move.line', string='Invoice line', readonly=True, ondelete='set null', copy=False)

    _unique_type_per_move = models.Constraint('UNIQUE(move_id, type_id)', 'An adjustment can be added to an invoice only once.')

    @api.constrains('value', 'type_id')
    def _check_value(self):
        for rec in self:
            if rec.value < 0:
                raise ValidationError(self.env._("The value of \"%s\" cannot be negative: choose Charge or Discount instead.",
                                                 rec.type_id.name))
            if rec.type_id.value_mode == 'percent' and rec.value > 100:
                raise ValidationError(self.env._("A percentage cannot exceed 100."))

    @api.constrains('move_id', 'type_id')
    def _check_applicable(self):
        for rec in self:
            if not rec.type_id._applies_to_move(rec.move_id):
                raise ValidationError(self.env._("\"%s\" is not available for this kind of document or company.", rec.type_id.name))

    @api.onchange('type_id')
    def _onchange_type_id(self):
        for rec in self:
            if rec.type_id:
                rec.tax_ids = rec.type_id.tax_ids
                rec.value = rec.value or rec.type_id.default_value

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            type_ = self.env['ncollection.invoice.adjustment.type'].browse(vals.get('type_id'))
            if type_ and 'tax_ids' not in vals:
                vals['tax_ids'] = [(6, 0, type_.tax_ids.ids)]
            if type_ and 'value' not in vals:
                vals['value'] = type_.default_value
        records = super().create(vals_list)
        records._nc_check_move_draft()
        records.move_id._nc_sync_adjustments()
        return records

    def write(self, vals):
        self._nc_check_move_draft()
        moves = self.move_id
        result = super().write(vals)
        (moves | self.move_id)._nc_sync_adjustments()
        return result

    def unlink(self):
        self._nc_check_move_draft()
        moves = self.move_id
        lines = self.line_id
        result = super().unlink()
        if lines.exists():
            lines.with_context(nc_adjustment_sync=True).unlink()
        moves._nc_sync_adjustments()
        return result

    def _nc_check_move_draft(self):
        for rec in self:
            if rec.move_id.state != 'draft' and not self.env.context.get('nc_adjustment_sync'):
                raise UserError(self.env._(
                    "Charges and discounts of a posted invoice cannot be changed. Reset it to draft, or issue a credit note."))
