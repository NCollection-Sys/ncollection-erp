# -*- coding: utf-8 -*-
"""Accounting's part of the Workspace Administration Center under Option A (see ncollection_core's
business_settings.py and res_company_delegation.py): the accounting settings and Accounting Defaults a workspace Owner
changes, now that Odoo's General Settings and a raw res.company write belong to the platform."""
from odoo import api, fields, models
from odoo.exceptions import ValidationError


def _native_selection(name):
    def selection(self):
        return self.env['res.config.settings']._fields[name]._description_selection(self.env)
    return selection


class ResCompany(models.Model):
    _inherit = 'res.company'

    @api.model
    def _nc_tenant_admin_company_fields(self):
        return super()._nc_tenant_admin_company_fields() | {
            # Business Settings > Accounting
            'account_sale_tax_id', 'account_purchase_tax_id', 'tax_calculation_rounding_method',
            'account_price_include', 'qr_code', 'display_invoice_amount_total_words', 'incoterm_id',
            # Workspace Administration > Accounting Defaults (views/accounting_defaults_views.xml)
            'nc_default_receivable_id', 'nc_default_payable_id', 'income_account_id', 'expense_account_id',
            'transfer_account_id', 'account_journal_suspense_account_id',
            'default_cash_difference_income_account_id', 'default_cash_difference_expense_account_id',
            'currency_exchange_journal_id', 'income_currency_exchange_account_id',
            'expense_currency_exchange_account_id', 'account_journal_early_pay_discount_gain_account_id',
            'account_journal_early_pay_discount_loss_account_id', 'revenue_accrual_account_id',
            'expense_accrual_account_id', 'automatic_entry_default_journal_id',
        }

    def _nc_check_delegated_values(self, vals):
        super()._nc_check_delegated_values(vals)
        for name, use in (('account_sale_tax_id', 'sale'), ('account_purchase_tax_id', 'purchase')):
            if vals.get(name) and self.env['account.tax'].sudo().browse(vals[name]).type_tax_use != use:
                raise ValidationError(self.env._(
                    "%(field)s must be a %(use)s tax.", field=self._fields[name].string, use=use))


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
