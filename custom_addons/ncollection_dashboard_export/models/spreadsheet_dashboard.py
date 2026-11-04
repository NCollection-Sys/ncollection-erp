# -*- coding: utf-8 -*-
# flake8: noqa: E501  (user-facing definitions are one translatable sentence each)
"""What the Dashboards page must tell the reader about the figures it shows.

Odoo's spreadsheet dashboards print bare numbers (``2,690``): the currency is not part of the card. This answers, for the
signed-in user's ACTIVE company, "which company, which currency" and, for the dashboards whose formulas were read and
checked against the ledger, "what does this figure mean" - in the words of what the formula really does.
"""
from odoo import api, models


class SpreadsheetDashboard(models.Model):
    _inherit = 'spreadsheet.dashboard'

    @api.model
    def _nc_definitions(self):
        _ = self.env._
        return {
            'spreadsheet_dashboard_account.dashboard_invoicing': [
                (_("Invoiced"), _("Untaxed amount of the customer invoice lines posted in the selected period, in the "
                                  "company currency.")),
                (_("Average Invoice"), _("Invoiced amount divided by the number of invoices in the period.")),
                (_("DSO (Days Sales Outstanding)"), _(
                    "As Odoo's Invoicing dashboard computes it: the receivable ledger balance of the current year divided by "
                    "(income minus direct costs) times 365. Read it with care: it divides by gross profit, not by revenue, "
                    "and the receivable balance is net of customer credit, so it overstates the days when direct costs "
                    "exist. The NCollection Finance dashboard shows what customers owe and the credit they hold separately. "
                    "A lower number is better.")),
            ],
            'spreadsheet_dashboard_sale.spreadsheet_dashboard_sales': [
                (_("Revenue"), _("Untaxed total of the confirmed sales orders (state Sales Order) in the period, in the "
                                 "company currency. Quotations are not revenue.")),
                (_("Average order amount"), _("Revenue divided by the number of confirmed orders.")),
                (_("Quotations"), _("Draft plus sent quotations that are not yet confirmed.")),
            ],
        }

    @api.model
    def nc_dashboard_context(self, dashboard_id):
        """``{company, currency, symbol, definitions}`` for one dashboard the user may read."""
        dashboard = self.browse(int(dashboard_id)).exists()
        if not dashboard:
            return {}
        dashboard.check_access('read')
        company = self.env.company
        currency = company.currency_id
        xmlid = dashboard.get_external_id().get(dashboard.id)
        companies = self.env.companies
        currencies = sorted(set(companies.mapped('currency_id.name')))
        return {
            'company': company.name, 'currency': currency.name, 'symbol': currency.symbol or currency.name,
            # Odoo's dashboards add up every TICKED company; with several currencies that sum is not one currency
            'companies': [{'name': c.name, 'currency': c.currency_id.name} for c in companies],
            'mixed_currencies': len(currencies) > 1,
            'definitions': [{'label': label, 'text': text} for label, text in self._nc_definitions().get(xmlid, [])],
        }
