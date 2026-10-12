# -*- coding: utf-8 -*-
"""Accounting Defaults: the default-account settings Odoo Community already has, in one place an Owner can reach.

NO second settings system. Every account below is a NATIVE ``res.company`` field (receivable/payable and category
accounts are native company-dependent properties whose default lives in ``ir.default``). Odoo exposes most of them
only inside the Invoicing app's settings, behind ``account.group_account_manager``, which no NCollection role holds
(the tenant admin gets it by accident of provisioning; an Owner invited later does not). This module adds the missing
doorway, Settings > Accounting Defaults, and nothing else:

* the two properties Odoo has NO settings field for (default receivable and payable account) are exposed as computed
  fields whose inverse writes the SAME ``ir.default`` row the chart loader wrote. (The product-category income and
  expense defaults need nothing new: Odoo already derives them from the company's own income / expense account.)
  A company-dependent property's ``ir.default`` is a live FALLBACK: it applies to every record that has no value of
  its own, new or old. A customer or vendor with an account set explicitly keeps it;
* everything is per company: ``ir.default`` is written for THIS company only, and the form edits the active company;
* writing is the existing rights of ``res.company`` / ``ir.default`` (system administrators, which the Owner role
  implies); no sudo is used to write, and the screen's menu is restricted to the same group (Standing Rule 4).

DEFERRED REVENUE / EXPENSE. Odoo 19 Community has no deferral engine (``deferred_*_account_id`` fields and the
generate-entries settings are Enterprise). What Community has, and what this exposes, are the accrual accounts and
journal the manual "Change Period" tool on journal items uses to spread an entry across periods. Nothing is invented
here that would pretend otherwise.
"""
from odoo import api, fields, models
from odoo.exceptions import ValidationError

_PARTNER = 'res.partner'


class ResCompany(models.Model):
    _inherit = 'res.company'

    nc_default_receivable_id = fields.Many2one(
        'account.account', string='Default receivable account', compute='_compute_nc_defaults',
        inverse='_inverse_nc_receivable', domain="[('account_type', '=', 'asset_receivable')]",
        help="Receivable account of every customer that has none set on its own record (including new customers). "
             "A customer with an account set on its own record keeps it.")
    nc_default_payable_id = fields.Many2one(
        'account.account', string='Default payable account', compute='_compute_nc_defaults',
        inverse='_inverse_nc_payable', domain="[('account_type', '=', 'liability_payable')]",
        help="Payable account of every vendor that has none set on its own record (including new vendors). "
             "A vendor with an account set on its own record keeps it.")

    # ------------------------------------------------------------------ the two ir.default properties
    _NC_PROPERTIES = {
        'nc_default_receivable_id': (_PARTNER, 'property_account_receivable_id'),
        'nc_default_payable_id': (_PARTNER, 'property_account_payable_id'),
    }

    @api.depends_context('company')
    def _compute_nc_defaults(self):
        # A read-only lookup of the default value for display; the WRITE below runs with the caller's own rights.
        defaults = self.env['ir.default'].sudo()
        for company in self:
            for name, (model, field) in self._NC_PROPERTIES.items():
                account_id = defaults._get(model, field, company_id=company.id)
                company[name] = self.env['account.account'].browse(account_id).exists() if account_id else False

    _NC_ACCOUNT_TYPE = {'nc_default_receivable_id': 'asset_receivable', 'nc_default_payable_id': 'liability_payable'}

    def _nc_set_default(self, name):
        model, field = self._NC_PROPERTIES[name]
        for company in self:
            account = company[name]
            if account:
                # The field's domain is only a hint in the form: refuse a wrong kind of account, or one that
                # belongs to another company, at the ORM (a bad default would fail later, at posting).
                if account.account_type != self._NC_ACCOUNT_TYPE[name] or company not in account.company_ids:
                    raise ValidationError(self.env._(
                        "%(account)s cannot be the %(field)s of %(company)s.",
                        account=account.display_name, field=self._fields[name].string, company=company.name))
            self.env['ir.default'].set(model, field, account.id or False, company_id=company.id)

    def _inverse_nc_receivable(self):
        self._nc_set_default('nc_default_receivable_id')

    def _inverse_nc_payable(self):
        self._nc_set_default('nc_default_payable_id')

    # ------------------------------------------------------------------ the screen
    @api.model
    def nc_action_accounting_defaults(self):
        """Settings > Accounting Defaults: the ACTIVE company's form."""
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._("Accounting Defaults"),
            'res_model': 'res.company',
            'res_id': self.env.company.id,
            'view_mode': 'form',
            'views': [(self.env.ref('ncollection_account_core.view_nc_accounting_defaults_form').id, 'form')],
            'target': 'current',
        }
