# -*- coding: utf-8 -*-
"""F2-T08: Native Financial Summary.

The executive view of financial health (FPA §Financial Summary, L1870): the
**eight** KPIs that spec names, with comparison columns. Every figure is
composed from the F2-T03 Balance Sheet / Profit & Loss services — see
``executive_base.py`` for why this layer never touches ``account.move.line``.

Performance KPIs (Revenue, Expenses, Net Profit) are a FLOW over the period.
Position KPIs (Cash, Receivables, Payables, Assets, Liabilities) are cumulative
AS OF ``date_to`` — the same split the Balance Sheet and P&L already make.
"""
from odoo import models

# The FPA §Financial Summary KPI list, in its documented order.
# (label, service key, level) — level 0 renders as a section head.
_SUMMARY_LAYOUT = [
    ("PERFORMANCE", None, 0),
    ("Revenue", 'revenue', 1),
    ("Expenses", 'expenses', 1),
    ("Net Profit", 'net_profit', 1),
    ("POSITION", None, 0),
    ("Cash", 'cash', 1),
    ("Receivables", 'receivables', 1),
    ("Payables", 'payables', 1),
    ("Assets", 'assets', 1),
    ("Liabilities", 'liabilities', 1),
]

# Position KPIs that are a single account_type rather than a Balance Sheet
# bucket. `sign` is -1 for credit-normal types, so all eight KPIs present as
# positive figures — an executive summary showing "Payables: -40,000" reads as
# a negative liability, which is not what the number means.
#
# The sign is ALREADY applied by the Balance Sheet bucket the account type sits in (a liability bucket has sign -1,
# so its balance arrives positive). This table used to flip payables a second time, and the Financial Summary showed
# a supplier debt as a NEGATIVE number (DASH-02, test_f_owed_minus_credit_is_the_ledger_balance).
_POSITION_TYPES = {
    'cash': ('asset_cash', 1),
    'receivables': ('asset_receivable', 1),
    'payables': ('liability_payable', 1),
}


class NcollectionFinancialSummary(models.TransientModel):
    _name = 'ncollection.account.report.summary'
    # Comparison first (supplies _nc_columns/_nc_list_view_ref), then the
    # engine, then the F2-T08 composition layer.
    _inherit = ['ncollection.account.report.comparison',
                'ncollection.account.report',
                'ncollection.account.report.executive']
    _description = 'NCollection Financial Summary'

    def _nc_report_title(self):
        return self.env._("Financial Summary")

    def _nc_label_column(self):
        return self.env._("KPI")

    def _nc_report_action_ref(self):
        return 'ncollection_account_reports.action_report_financial_summary'

    # ---- the service surface (#56 consumes THIS, not the rendered rows) ---

    def _nc_service_figures(self):
        self.ensure_one()
        return self._nc_service_figures_for(self.date_from, self.date_to)

    def _nc_service_figures_for(self, date_from, date_to, open_items=True):
        """The eight FPA KPIs for an explicit period.

        Revenue is TOTAL income (sales + other operating), and Expenses is cost
        of sales + operating expenses — so that Revenue − Expenses reconciles
        with Net Profit exactly as the P&L computes it. Reporting only the
        `revenue` bucket here would leave the three KPIs not adding up, which is
        the first thing an executive checks.
        """
        self.ensure_one()
        pl = self._nc_pl(date_from, date_to)
        # One Balance Sheet evaluation serves both the bucket totals and the
        # per-account_type figures — not two aggregates over the same scope.
        bs, by_type = self._nc_bs_full(date_to)
        figures = {
            'revenue': pl['total_income'],
            'expenses': pl['cogs'] + pl['operating_expenses'],
            'net_profit': pl['net_profit'],
            'assets': bs['current_assets'] + bs['fixed_assets'],
            'liabilities': bs['current_liabilities'] + bs['long_term_liabilities'],
        }
        for key, (account_type, sign) in _POSITION_TYPES.items():
            figures[key] = by_type.get(account_type, 0.0) * sign
        if not open_items:                       # a trend needs the ledger figures only
            return figures
        # The ledger figures above NET a partner's credit against what they owe. The open items on each side, as of
        # the same date, are what the dashboards show as "owed" (owed - credit = the ledger figure, tested).
        items = self.env['ncollection.account.open.items']
        draft = self.target_move != 'posted'
        owed, credit = items.position('asset_receivable', date_to, self.company_id, draft)
        payable_owed, prepayments, credit_notes = items.payable_position(date_to, self.company_id, draft)
        figures.update({
            'receivables_owed': owed, 'customer_credit': credit,
            'payables_owed': payable_owed, 'vendor_prepayments': prepayments, 'vendor_credit_notes': credit_notes,
        })
        return figures

    # ---- rendering --------------------------------------------------------

    def _nc_compute_lines(self):
        return self._nc_metric_rows(_SUMMARY_LAYOUT)
