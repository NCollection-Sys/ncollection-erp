# -*- coding: utf-8 -*-
"""Accounting's part of the Workspace Administration Center under Option A (see ncollection_core's
business_settings.py and res_company_delegation.py): the accounting settings and Accounting Defaults a workspace Owner
changes, now that Odoo's General Settings and a raw res.company write belong to the platform."""
from odoo import api, models
from odoo.exceptions import ValidationError


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

    def write(self, vals):
        """Odoo's account overrides res.company.write and sets ir.default (the default receivable / payable / income
        accounts) as the CALLER before calling super - so, when account loads after ncollection_core, a Workspace
        Administrator's delegated write was refused by ir.default's record rule before the delegation was reached.
        This module loads after account, so its override runs first: the same checks, elevated here, audited once."""
        if self._nc_delegated_company_write(vals):
            self._nc_check_delegated_values(vals)
            result = super(ResCompany, self.sudo()).write(vals)
            self._nc_audit_delegated_write(vals)
            return result
        return super().write(vals)

    def _nc_check_delegated_values(self, vals):
        result = super()._nc_check_delegated_values(vals)
        for name, use in (('account_sale_tax_id', 'sale'), ('account_purchase_tax_id', 'purchase')):
            if vals.get(name) and self.env['account.tax'].sudo().browse(vals[name]).type_tax_use != use:
                raise ValidationError(self.env._(
                    "%(field)s must be a %(use)s tax.", field=self._fields[name].string, use=use))
        return result
