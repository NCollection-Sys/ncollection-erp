# -*- coding: utf-8 -*-
"""Request 07: the Calculation Rules dialog (see models/calculation_rules.py for the rule itself)."""
from odoo import api, fields, models
from odoo.exceptions import AccessError

from ..models.calculation_rules import TIMING


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
        policy = self.env.get('ncollection.settings.policy')
        if policy is not None:
            policy._nc_require('accounting_defaults', 'edit')     # the platform's Unified Settings level
        self.company_id.sudo().nc_discount_tax_timing = self.discount_tax_timing
        if hasattr(self.env.user, '_nc_audit'):
            self.env.user._nc_audit('admin_settings_change', 'company %s: nc_discount_tax_timing=%s' % (
                self.company_id.id, self.discount_tax_timing))
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
            'type': 'success', 'title': self.env._("Calculation Rules saved"),
            'message': self.env._("Draft invoices use them when next recalculated; posted invoices are unchanged."),
            'next': {'type': 'ir.actions.act_window_close'}}}
