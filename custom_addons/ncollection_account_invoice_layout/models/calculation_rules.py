# -*- coding: utf-8 -*-
"""Request 07: Calculation Rules — whether an invoice-level DISCOUNT is applied before or after tax.

Both treatments are booked by the existing adjustment engine (invoice_adjustment.py), never by editing totals:
  * Discount BEFORE tax (default): the discount line is split over the VAT groups of the invoice lines, pro rata, so
    each taxable base is reduced by its share. 1,000 - 10 % = 900, VAT 5 % = 45, total 945.
  * Discount AFTER tax: the discount line carries NO tax, so the VAT stays computed on the undiscounted base.
    1,000, VAT 50, discount 100, total 950. The journal entry balances (revenue 1,000, discount -100, VAT 50,
    receivable 950) and the VAT return reports the base and tax actually invoiced; tax is never understated.

It is a COMPANY rule for discount types set to "Calculation Rules of the company" (the default for discount types);
a type with explicit taxes keeps them. Only draft invoices are recalculated: a posted invoice keeps what it was
posted with. Whether the after-tax treatment is appropriate is a tax question for the company (under UAE VAT a
discount given at the time of supply normally reduces the value of supply), so the setting says so where it is made.
"""
from odoo import api, fields, models
from odoo.exceptions import AccessError

TIMING = [
    ('before_tax', 'Discount before tax (reduces the taxable base)'),
    ('after_tax', 'Discount after tax (tax on the undiscounted base; the discount carries no tax)'),
]


class ResCompany(models.Model):
    _inherit = 'res.company'

    nc_discount_tax_timing = fields.Selection(TIMING, string='Invoice Discounts', default='before_tax',
                                              required=True)


class InvoiceCalculationRules(models.TransientModel):
    _name = 'ncollection.invoice.calculation.rules'
    _description = 'Invoice Calculation Rules'

    company_id = fields.Many2one('res.company', required=True, readonly=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')
    discount_tax_timing = fields.Selection(
        TIMING, string='Invoice Discounts', required=True,
        default=lambda self: self.env.company.nc_discount_tax_timing)
    is_uae = fields.Boolean(compute='_compute_preview')
    # the preview: an example, never an invoice
    sample_subtotal = fields.Monetary(string='Example Subtotal', default=1000.0)
    sample_discount_pct = fields.Float(string='Discount %', default=10.0)
    sample_tax_pct = fields.Float(string='Tax %', default=5.0)
    before_discount = fields.Monetary(compute='_compute_preview')
    before_tax = fields.Monetary(compute='_compute_preview')
    before_total = fields.Monetary(compute='_compute_preview')
    after_discount = fields.Monetary(compute='_compute_preview')
    after_tax = fields.Monetary(compute='_compute_preview')
    after_total = fields.Monetary(compute='_compute_preview')

    @api.depends('sample_subtotal', 'sample_discount_pct', 'sample_tax_pct', 'company_id')
    def _compute_preview(self):
        for rec in self:
            cur = rec.currency_id or self.env.company.currency_id
            base, pct, tax = rec.sample_subtotal, rec.sample_discount_pct / 100.0, rec.sample_tax_pct / 100.0
            discount = cur.round(base * pct)
            rec.before_discount = discount
            rec.before_tax = cur.round((base - discount) * tax)
            rec.before_total = cur.round(base - discount + rec.before_tax)
            rec.after_discount = discount
            rec.after_tax = cur.round(base * tax)
            rec.after_total = cur.round(base + rec.after_tax - discount)
            rec.is_uae = rec.company_id.account_fiscal_country_id.code == 'AE'

    def action_save(self):
        self.ensure_one()
        if not self.env.user.has_group('account.group_account_manager'):
            raise AccessError(self.env._("Only an accounting administrator can change the calculation rules."))
        if self.company_id not in self.env.user.company_ids:
            raise AccessError(self.env._("You cannot change the rules of this company."))
        self.company_id.sudo().nc_discount_tax_timing = self.discount_tax_timing
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
            'type': 'success', 'title': self.env._("Calculation Rules saved"),
            'message': self.env._("Draft invoices use them when next recalculated; posted invoices are unchanged."),
            'next': {'type': 'ir.actions.act_window_close'}}}
