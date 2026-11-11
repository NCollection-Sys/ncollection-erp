# -*- coding: utf-8 -*-
"""Invoice-level charges and discounts (freight, a global discount, any other configured adjustment).

ARCHITECTURE. An adjustment is DATA, never code: the administrator configures ``ncollection.invoice.adjustment.type``
records (name, charge or discount, fixed or percentage, basis, account, taxes, analytic distribution, applicability).
An invoice carries ``ncollection.invoice.adjustment`` rows (type + the user's value). Each row with a non-zero amount
is
booked as a REAL invoice line (quantity 1, price = +amount for a charge, -amount for a discount, the type's account,
taxes
and analytic distribution), so the journal entry, the taxes, the receivable / payable and every reconciliation are
Odoo's own. Nothing edits ``amount_total``; there is no expression language and nothing is evaluated.

CALCULATION ORDER (also in docs/markdown/INVOICE_LAYOUT_AND_CALCULATIONS.md)
  1. line discounts (Disc.% / fixed) -> P = sum of the discounted, tax-excluded PRODUCT lines. Adjustment lines are
  never
     part of any basis, so an adjustment cannot feed itself or another one's percentage.
  2. discounts: fixed amount, or percentage of P.  G = their total.
  3. charges:   fixed amount, or percentage of P (basis "lines") or of P - G (basis "lines after discount").
  4. taxes, by the TAX MODE of the type:
     * "The taxes below" (fixed): one booked line carrying the taxes of the type (editable per invoice). A VAT-able
       freight charge carries the sales VAT; a charge outside the VAT scope carries none.
     * "Same taxes as the invoice lines" (lines): the adjustment is split over the tax groups of the product lines,
       pro rata to each group's tax-excluded subtotal, one booked line per group with that group's taxes. This is the
       sound treatment of a global discount over lines with different VAT rates (5 %, 0 %, exempt...): each VAT base
       is reduced by its own share, so the VAT of each rate is exact. The rounding remainder goes to the largest group.
     Odoo's own tax engine computes every tax; a price-included tax is grossed up so the booked tax-excluded amount is
     exactly the adjustment's.
  Untaxed Amount = P - G + F (F = charges). Total Discount = line discounts + G. Total After Discount = P - G.

Only DRAFT invoices change; a posted invoice keeps the lines it was posted with.

PRESENTATION. The booked lines are accounting records (Journal Items) and stay in ``invoice_line_ids``, so taxes,
totals, the invoice amount audit and the e-invoice keep reading one truth. They are identified by
``account.move.line.nc_adjustment_id`` and are therefore left OUT of the commercial item list: the invoice form's
Invoice Lines table (commercial_lines.js) and the printed product table (``_get_move_lines_to_report``). The form shows
them in the Charges & Adjustments block, the PDF in its own Charges & Discounts section; never twice.
"""

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

KINDS = [('charge', 'Charge'), ('discount', 'Discount')]


class InvoiceAdjustmentType(models.Model):
    _name = 'ncollection.invoice.adjustment.type'
    _description = 'Invoice charge / discount definition'
    _inherit = ['analytic.mixin', 'mail.thread']
    _order = 'sequence, id'

    name = fields.Char(string='Label', required=True, translate=True, tracking=True)
    active = fields.Boolean(default=True, tracking=True)
    sequence = fields.Integer(default=10)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company, index=True)
    kind = fields.Selection(KINDS, required=True, default='charge', tracking=True)
    value_mode = fields.Selection(
        [('fixed', 'Fixed amount'), ('percent', 'Percentage')], string='Value', required=True, default='fixed'
    )
    default_value = fields.Float(string='Default value', digits=(16, 4), tracking=True)
    basis = fields.Selection(
        [
            ('lines', 'Invoice lines (after line discounts)'),
            ('lines_after_discount', 'Invoice lines after the invoice discounts'),
        ],
        string='Percentage of',
        default='lines',
        required=True,
        help="Only for a percentage. Discounts always use the first basis; a charge may use the second.",
    )
    applies_to = fields.Selection(
        [
            ('both', 'Customer invoices and vendor bills'),
            ('customer', 'Customer invoices only'),
            ('vendor', 'Vendor bills only'),
        ],
        default='both',
        required=True,
    )
    auto_add = fields.Boolean(
        string='On every new invoice', help="A new invoice starts with this adjustment, ready for its value."
    )
    account_id = fields.Many2one(
        'account.account',
        required=True,
        check_company=True,
        domain="[('account_type', 'not in', ('asset_receivable', 'liability_payable', 'off_balance'))]",
    )
    tax_mode = fields.Selection(
        [
            ('lines', 'Same taxes as the invoice lines (split pro rata)'),
            ('fixed', 'The taxes below'),
        ],
        string='Taxes from',
        required=True,
        default='fixed',
        tracking=True,
        help="Same taxes as the invoice lines: the amount is split over the VAT groups of the invoice lines, "
        "pro rata, so each VAT base is reduced (or increased) by its own share; use it for a global discount over several VAT "
        "rates. The taxes below: one line with exactly these taxes; use it for a freight or service charge.",
    )
    tax_ids = fields.Many2many(
        'account.tax',
        string='Taxes',
        check_company=True,
        help="The taxes of the booked line when Taxes from is 'The taxes below'. A VAT-able freight charge "
        "carries the sales VAT; a charge outside the VAT scope carries none.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            # a discount follows the taxes of the lines it reduces unless the administrator says otherwise
            if 'tax_mode' not in vals and vals.get('kind') == 'discount':
                vals['tax_mode'] = 'lines'
        return super().create(vals_list)

    @api.onchange('kind')
    def _onchange_kind_tax_mode(self):
        for rec in self:
            rec.tax_mode = 'lines' if rec.kind == 'discount' else 'fixed'

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
        if {'account_id', 'tax_ids', 'kind', 'name', 'tax_mode'} & set(vals):
            drafts = self.env['ncollection.invoice.adjustment'].search(
                [('type_id', 'in', self.ids), ('move_id.state', '=', 'draft')]
            )
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
    type_id = fields.Many2one(
        'ncollection.invoice.adjustment.type',
        string='Adjustment',
        required=True,
        ondelete='restrict',
        domain="[('company_id', '=', company_id)]",
    )
    kind = fields.Selection(related='type_id.kind', store=True)
    value_mode = fields.Selection(related='type_id.value_mode')
    sequence = fields.Integer(related='type_id.sequence', store=True)
    value = fields.Float(digits=(16, 4), help="An amount, or a percentage when the adjustment is a percentage.")
    tax_mode = fields.Selection(related='type_id.tax_mode')
    tax_ids = fields.Many2many('account.tax', string='Taxes', check_company=True)
    amount = fields.Monetary(
        currency_field='currency_id',
        readonly=True,
        help="The money booked, tax-excluded and always positive; a discount is booked negative.",
    )
    line_ids = fields.One2many(
        'account.move.line',
        'nc_adjustment_id',
        string='Booked lines',
        readonly=True,
        help="The journal items that book this adjustment: one, or one per VAT group when the taxes follow the lines.",
    )

    _unique_type_per_move = models.Constraint(
        'UNIQUE(move_id, type_id)', 'An adjustment can be added to an invoice only once.'
    )

    @api.constrains('value', 'type_id')
    def _check_value(self):
        for rec in self:
            if rec.value < 0:
                raise ValidationError(
                    self.env._(
                        "The value of \"%s\" cannot be negative: choose Charge or Discount instead.", rec.type_id.name
                    )
                )
            if rec.type_id.value_mode == 'percent' and rec.value > 100:
                raise ValidationError(self.env._("A percentage cannot exceed 100."))

    @api.constrains('move_id', 'type_id')
    def _check_applicable(self):
        for rec in self:
            if not rec.type_id._applies_to_move(rec.move_id):
                raise ValidationError(
                    self.env._("\"%s\" is not available for this kind of document or company.", rec.type_id.name)
                )

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
        lines = self.line_ids
        result = super().unlink()
        if lines.exists():
            lines.with_context(nc_adjustment_sync=True).unlink()
        moves._nc_sync_adjustments()
        return result

    def _nc_check_move_draft(self):
        for rec in self:
            if rec.move_id.state != 'draft' and not self.env.context.get('nc_adjustment_sync'):
                raise UserError(
                    self.env._(
                        "Charges and discounts of a posted invoice cannot be changed. Reset it to draft, or issue"
                        " a credit note."
                    )
                )
