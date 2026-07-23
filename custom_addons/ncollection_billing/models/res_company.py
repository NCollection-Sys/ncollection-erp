# -*- coding: utf-8 -*-
"""Company-scoped billing setup for the admin DB (P2-T11).

Holds the get-or-create helpers for the accounting prerequisites so both the
post-install hook and the runtime billing path resolve the SAME records.
"""
from odoo import models

# Stable markers used to find-or-create the singletons (no XML ids: these are
# created after the chart template loads, which XML data cannot depend on).
_BILLING_PRODUCT_CODE = 'NC-SUB'
_GENERIC_CHART = 'generic_coa'


class ResCompany(models.Model):
    _inherit = 'res.company'

    def _nc_ensure_billing_setup(self):
        """Idempotently ensure a Chart of Accounts + AED currency + VAT tax + product."""
        self.ensure_one()
        if not self.chart_template:
            # Odoo 19 loads chart templates in code; the generic template ships
            # with `account`, so no localization module is needed. (The full UAE
            # chart of accounts is a separate Phase-3 deliverable — P3-T05.)
            self.env['account.chart.template'].try_loading(
                _GENERIC_CHART, company=self, install_demo=False)
        self._nc_ensure_currency()
        self._nc_billing_tax()
        self._nc_billing_product()

    def _nc_ensure_currency(self):
        """Bill in AED. Subscription invoices carry UAE VAT 5%, so the platform
        company must transact in AED — not the generic template's USD default.
        Odoo forbids changing a company's currency once it has journal entries,
        so only switch a still-empty company; a no-op once set."""
        self.ensure_one()
        aed = self.env.ref('base.AED', raise_if_not_found=False)
        if not aed or self.currency_id == aed:
            return
        has_entries = self.env['account.move.line'].sudo().search_count(  # arch-guard: admin-db-billing
            [('company_id', '=', self.id)], limit=1)
        if has_entries:
            return  # currency is locked once accounting has started
        if not aed.active:
            aed.active = True
        self.currency_id = aed

    def _nc_billing_tax(self):
        """The 5% UAE VAT sales tax for subscription invoices."""
        self.ensure_one()
        Tax = self.env['account.tax']
        tax = Tax.search([
            ('company_id', '=', self.id), ('type_tax_use', '=', 'sale'),
            ('amount_type', '=', 'percent'), ('amount', '=', 5.0),
        ], limit=1)
        if not tax:
            tax = Tax.create({
                'name': 'VAT 5%', 'amount': 5.0, 'amount_type': 'percent',
                'type_tax_use': 'sale', 'company_id': self.id,
            })
        return tax

    def _nc_billing_product(self):
        """The service product every subscription invoice line points at."""
        Product = self.env['product.product']  # arch-guard: admin-db-billing
        product = Product.search([('default_code', '=', _BILLING_PRODUCT_CODE)], limit=1)
        if not product:
            product = Product.create({
                'name': 'NCollection Subscription',
                'default_code': _BILLING_PRODUCT_CODE,
                'type': 'service',
                'list_price': 0.0,
            })
        return product
