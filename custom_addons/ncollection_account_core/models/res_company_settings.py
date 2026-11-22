# -*- coding: utf-8 -*-
"""Accounting's part of the Workspace Administration Center under Option A (see ncollection_core's
business_settings.py and res_company_delegation.py): the accounting settings and Accounting Defaults a workspace Owner
changes, now that Odoo's General Settings and a raw res.company write belong to the platform."""
from odoo import api, models
from odoo.exceptions import AccessError, ValidationError

# Settings > Accounting fields stored on res.company: a Workspace Administrator only (never a section delegate).
# The Accounting Defaults section keeps its own, delegable, default accounts.
ACCOUNTING_CONTROL_FIELDS = frozenset((
    'account_sale_tax_id', 'account_purchase_tax_id', 'tax_calculation_rounding_method', 'account_price_include',
    'qr_code', 'display_invoice_amount_total_words', 'incoterm_id', 'tax_exigibility', 'tax_cash_basis_journal_id',
    'account_cash_basis_base_account_id', 'display_invoice_tax_company_currency', 'quick_edit_mode',
    'autopost_bills', 'account_use_credit_limit', 'account_discount_income_allocation_id',
    'account_discount_expense_allocation_id',
))


class ResCompany(models.Model):
    _inherit = 'res.company'

    @api.model
    def _nc_tenant_admin_company_fields(self):
        return super()._nc_tenant_admin_company_fields() | {
            # Business Settings > Accounting
            'account_sale_tax_id', 'account_purchase_tax_id', 'tax_calculation_rounding_method',
            'account_price_include', 'qr_code', 'display_invoice_amount_total_words', 'incoterm_id',
            'tax_exigibility', 'tax_cash_basis_journal_id', 'account_cash_basis_base_account_id',
            'display_invoice_tax_company_currency', 'quick_edit_mode', 'autopost_bills', 'account_use_credit_limit',
            'account_discount_income_allocation_id',
            'account_discount_expense_allocation_id',
            # (Main Currency, Fiscal Country and Chart of Accounts are NOT here: only the Settings page's own
            #  handlers apply them, after the accounting-entries guard - wizards/business_settings.py)
            # Workspace Administration > Accounting Defaults (views/accounting_defaults_views.xml)
            'nc_default_receivable_id', 'nc_default_payable_id', 'income_account_id', 'expense_account_id',
            'transfer_account_id', 'account_journal_suspense_account_id',
            'default_cash_difference_income_account_id', 'default_cash_difference_expense_account_id',
            'currency_exchange_journal_id', 'income_currency_exchange_account_id',
            'expense_currency_exchange_account_id', 'account_journal_early_pay_discount_gain_account_id',
            'account_journal_early_pay_discount_loss_account_id', 'revenue_accrual_account_id',
            'expense_accrual_account_id', 'automatic_entry_default_journal_id',
        }

    @api.model
    def _nc_delegated_form_views(self):
        return {**super()._nc_delegated_form_views(),
                'ncollection_account_core.view_nc_accounting_defaults_form': 'accounting_defaults'}

    @api.model
    def _nc_delegated_writable_fields(self):
        allowed = super()._nc_delegated_writable_fields()
        if not self.env.user.has_group('ncollection_core.group_tenant_admin'):
            allowed -= ACCOUNTING_CONTROL_FIELDS   # the same refusal _nc_check_delegated_values applies on save
        return allowed

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
        if set(vals) & ACCOUNTING_CONTROL_FIELDS and not self.env.user.has_group(
                'ncollection_core.group_tenant_admin'):
            # a delegate of the Business Settings section never changes the company's accounting configuration
            raise AccessError(self.env._("Only a workspace administrator can change the accounting settings."))
        for name, use in (('account_sale_tax_id', 'sale'), ('account_purchase_tax_id', 'purchase')):
            if vals.get(name) and self.env['account.tax'].sudo().browse(vals[name]).type_tax_use != use:
                raise ValidationError(self.env._(
                    "%(field)s must be a %(use)s tax.", field=self._fields[name].string, use=use))
        # Odoo enforces these only in its settings VIEW (domains, onchange); a delegated write is checked here.
        journal = vals.get('tax_cash_basis_journal_id')
        if journal and self.env['account.journal'].sudo().browse(journal).type != 'general':
            raise ValidationError(self.env._("The cash basis journal must be a miscellaneous journal."))
        # Accounting Defaults: the field domains Odoo declares on res.company (the ORM does not enforce a domain)
        for name in ('currency_exchange_journal_id', 'automatic_entry_default_journal_id'):
            if vals.get(name) and self.env['account.journal'].sudo().browse(vals[name]).type != 'general':
                raise ValidationError(self.env._("%(field)s must be a miscellaneous journal.",
                                                 field=self._fields[name].string))
        not_partner = ('asset_receivable', 'liability_payable')
        account_rules = {
            'income_currency_exchange_account_id': lambda a: a.internal_group == 'income',
            'expense_currency_exchange_account_id': lambda a: a.account_type in ('expense', 'expense_other'),
            'expense_accrual_account_id':
                lambda a: a.internal_group == 'liability' and a.account_type not in not_partner,
            'revenue_accrual_account_id':
                lambda a: a.internal_group == 'asset' and a.account_type not in not_partner,
        }
        for name, allowed in account_rules.items():
            if vals.get(name) and not allowed(self.env['account.account'].sudo().browse(vals[name])):
                raise ValidationError(self.env._("%(field)s does not accept this type of account.",
                                                 field=self._fields[name].string))
        types = ('income', 'income_other', 'expense', 'expense_other')   # Odoo's own settings domain
        for name in ('account_discount_income_allocation_id', 'account_discount_expense_allocation_id'):
            if vals.get(name) and self.env['account.account'].sudo().browse(vals[name]).account_type not in types:
                raise ValidationError(self.env._("%(field)s must be an income or expense account.",
                                                 field=self._fields[name].string))
        if self.filtered('parent_id') and {'tax_exigibility', 'account_storno'} & set(vals):
            # root-delegated in Odoo (_get_company_root_delegated_field_names): set on the parent company
            raise ValidationError(self.env._("A branch follows its parent company for this setting."))
        if 'tax_exigibility' in vals and not vals['tax_exigibility'] and self.env['account.tax'].sudo().search_count(
                [('company_id', 'child_of', self.ids), ('tax_exigibility', '=', 'on_payment')], limit=1):
            # Odoo's _onchange_tax_exigibility, at the ORM
            raise ValidationError(self.env._(
                "Cash basis taxes cannot be turned off while taxes based on payment exist."))
        return result
