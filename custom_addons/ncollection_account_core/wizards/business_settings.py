# -*- coding: utf-8 -*-
"""Accounting's part of the Workspace Administration Center under Option A (see ncollection_core's
business_settings.py and res_company_delegation.py): the accounting settings and Accounting Defaults a workspace Owner
changes, now that Odoo's General Settings and a raw res.company write belong to the platform."""
from odoo import api, fields, models


def _native_selection(name):
    def selection(self):
        return self.env['res.config.settings']._fields[name]._description_selection(self.env)
    return selection


class NcBusinessSettings(models.TransientModel):
    _inherit = 'ncollection.business.settings'

    sale_tax_id = fields.Many2one(
        'account.tax', string='Default Sales Tax',
        domain="[('type_tax_use', '=', 'sale'), ('company_id', 'parent_of', company_id)]")
    purchase_tax_id = fields.Many2one(
        'account.tax', string='Default Purchase Tax',
        domain="[('type_tax_use', '=', 'purchase'), ('company_id', 'parent_of', company_id)]")
    tax_calculation_rounding_method = fields.Selection(
        _native_selection('tax_calculation_rounding_method'), string='Tax Rounding Method')
    account_price_include = fields.Selection(
        _native_selection('account_price_include'), string='Default Sales Price Include')
    qr_code = fields.Boolean(string='QR Codes on Invoices')
    display_invoice_amount_total_words = fields.Boolean(string='Total Amount in Words')
    incoterm_id = fields.Many2one('account.incoterms', string='Default Incoterm')
    group_cash_rounding = fields.Boolean(string='Cash Rounding')
    group_sale_delivery_address = fields.Boolean(string='Customer Delivery Addresses')
    use_invoice_terms = fields.Boolean(string='Default Terms & Conditions')

    @api.model
    def _nc_settings_spec(self):
        spec = super()._nc_settings_spec()
        spec.update(dict.fromkeys((
            'sale_tax_id', 'purchase_tax_id', 'tax_calculation_rounding_method', 'account_price_include', 'qr_code',
            'display_invoice_amount_total_words', 'incoterm_id', 'group_cash_rounding', 'group_sale_delivery_address',
            'use_invoice_terms'), 'native'))
        return spec
