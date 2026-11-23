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
from odoo.exceptions import ValidationError

TIMING = [
    ('before_tax', 'Discount before tax (reduces the taxable base)'),
    ('after_tax', 'Discount after tax (tax on the undiscounted base; the discount carries no tax)'),
]


class ResCompany(models.Model):
    _inherit = 'res.company'

    nc_discount_tax_timing = fields.Selection(TIMING, string='Invoice Discounts', default='before_tax',
                                              required=True)

    def _nc_after_tax_refused(self):
        """A reason the after-tax treatment is refused for this company, or ''. UAE VAT: a discount given at the
        time of supply reduces the value of supply (VAT on 900, not 1,000), and a PINT-AE allowance must carry a
        VAT category; so it is refused for a UAE company and wherever UAE e-invoicing is installed."""
        self.ensure_one()
        if self.account_fiscal_country_id.code == 'AE' or self.country_id.code == 'AE':
            return self.env._("UAE VAT: a discount given at the time of supply reduces the value of supply, so the "
                              "discount is applied before tax.")
        if self.env['ir.module.module'].sudo().search_count(
                [('name', '=', 'ncollection_uae_einvoicing'), ('state', '=', 'installed')]):
            return self.env._("UAE e-invoicing is installed: an invoice allowance must carry a VAT category, so the "
                              "discount is applied before tax.")
        return ''

    @api.constrains('nc_discount_tax_timing', 'account_fiscal_country_id', 'country_id')
    def _nc_check_discount_tax_timing(self):
        for company in self.filtered(lambda c: c.nc_discount_tax_timing == 'after_tax'):
            reason = company._nc_after_tax_refused()
            if reason:
                raise ValidationError(reason)
