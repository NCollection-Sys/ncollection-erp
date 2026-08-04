# -*- coding: utf-8 -*-
"""F2-T08: Native Revenue Analysis and Expense Analysis.

⚠ SPEC NOTE — read before changing these.
`FINANCIAL_PLATFORM_ARCHITECTURE.md` §10 lists "Revenue Analysis" and "Expense
Analysis" in its Executive Reports **catalog** (L955-960) but, unlike Balance
Sheet / P&L / Financial Summary, gives them **no specification section** — no
metrics, no columns, no acceptance criteria. The shape below is therefore
DERIVED, and the derivation is deliberate rather than invented:

* the metric set follows §Cost Center Analysis (L1818), the closest documented
  analogue: *Revenue · Expense · Profit · Margin*;
* the account grouping reuses the P&L buckets verbatim, so an Analysis figure
  and the P&L line it came from can never disagree;
* the comparison columns are the FPA set every other report in this module uses.

If a real spec is later written, THIS is the file to reconcile against it.

Both reports are a FLOW over the period (P&L semantics), so the engine's
period-bounded drill-down is already correct — no override needed, unlike the
Balance Sheet.
"""
from odoo import models


class NcollectionRevenueExpenseAnalysisBase(models.AbstractModel):
    """Shared shape: a set of P&L buckets, expanded per account, with a total."""
    _name = 'ncollection.account.report.analysis.base'
    _description = 'NCollection Revenue/Expense Analysis Base'

    def _nc_label_column(self):
        return self.env._("Account")

    def _nc_analysis_buckets(self):
        """``[(bucket key, label)]`` from the P&L this report analyses."""
        raise NotImplementedError

    def _nc_total_key(self):
        """The service key holding this report's grand total."""
        raise NotImplementedError

    def _nc_total_label(self):
        raise NotImplementedError

    # ---- service surface --------------------------------------------------

    def _nc_service_figures(self):
        self.ensure_one()
        return self._nc_service_figures_for(self.date_from, self.date_to)

    def _nc_service_figures_for(self, date_from, date_to):
        """Bucket totals plus this report's grand total, from the P&L."""
        self.ensure_one()
        figures = self._nc_pl(date_from, date_to)
        return {key: figures.get(key, 0.0)
                for key, _label in self._nc_analysis_buckets()} | {
            self._nc_total_key(): sum(
                figures.get(key, 0.0) for key, _label in self._nc_analysis_buckets())}

    # ---- rendering --------------------------------------------------------

    def _nc_compute_lines(self):
        """Bucket subtotal, then its accounts, then the grand total.

        Per-account rows span the UNION of both periods — an account that
        earned nothing this period but did last one still belongs on a
        comparison report, and omitting it would make the visible rows fail to
        add up to the subtotal above them (the F2-T03 lesson).
        """
        self.ensure_one()
        current, current_detail = self._nc_pl_full(self.date_from, self.date_to)
        window = self._nc_comparison_range()
        previous, previous_detail = (
            self._nc_pl_full(*window) if window else ({}, {}))
        previous_by_account = {
            account.id: balance
            for rows in previous_detail.values() for account, balance in rows}

        rows = []
        grand_current = grand_previous = 0.0
        for key, label in self._nc_analysis_buckets():
            bucket_current = current.get(key, 0.0)
            bucket_previous = previous.get(key, 0.0)
            grand_current += bucket_current
            grand_previous += bucket_previous
            rows.append(self._nc_comparison_row(
                label, bucket_current, bucket_previous, level=1))
            for account, balance, prior in self._nc_union_accounts(
                    current_detail.get(key, ()), previous_detail.get(key, ()),
                    previous_by_account):
                rows.append(self._nc_comparison_row(
                    account.display_name, balance, prior,
                    level=2, account_id=account.id))
        rows.append(self._nc_comparison_row(
            self._nc_total_label(), grand_current, grand_previous, level=0))
        return rows

    @staticmethod
    def _nc_union_accounts(current_rows, previous_rows, previous_by_account):
        """``[(account, current, previous)]`` over both periods, by code."""
        seen = {account.id for account, _balance in current_rows}
        merged = [(account, balance, previous_by_account.get(account.id, 0.0))
                  for account, balance in current_rows]
        merged += [(account, 0.0, prior)
                   for account, prior in previous_rows if account.id not in seen]
        return sorted(merged, key=lambda row: row[0].code or '')


class NcollectionRevenueAnalysis(models.TransientModel):
    _name = 'ncollection.account.report.revenue'
    # ORDER IS LOAD-BEARING. Odoo assembles bases in _inherit order and C3
    # linearises them the same way, so the FIRST entry defining a method wins.
    # analysis.base must precede executive (whose _nc_service_figures is
    # abstract) and the engine (whose _nc_compute_lines is abstract), or the
    # concrete implementations below are shadowed by NotImplementedError.
    _inherit = ['ncollection.account.report.analysis.base',
                'ncollection.account.report.comparison',
                'ncollection.account.report',
                'ncollection.account.report.executive']
    _description = 'NCollection Revenue Analysis'

    def _nc_report_title(self):
        return self.env._("Revenue Analysis")

    def _nc_report_action_ref(self):
        return 'ncollection_account_reports.action_report_revenue_analysis'

    def _nc_analysis_buckets(self):
        return [('revenue', self.env._("Sales Revenue")),
                ('other_income', self.env._("Other Operating Income"))]

    def _nc_total_key(self):
        return 'total_income'

    def _nc_total_label(self):
        return self.env._("TOTAL REVENUE")


class NcollectionExpenseAnalysis(models.TransientModel):
    _name = 'ncollection.account.report.expense'
    # ORDER IS LOAD-BEARING. Odoo assembles bases in _inherit order and C3
    # linearises them the same way, so the FIRST entry defining a method wins.
    # analysis.base must precede executive (whose _nc_service_figures is
    # abstract) and the engine (whose _nc_compute_lines is abstract), or the
    # concrete implementations below are shadowed by NotImplementedError.
    _inherit = ['ncollection.account.report.analysis.base',
                'ncollection.account.report.comparison',
                'ncollection.account.report',
                'ncollection.account.report.executive']
    _description = 'NCollection Expense Analysis'

    def _nc_report_title(self):
        return self.env._("Expense Analysis")

    def _nc_report_action_ref(self):
        return 'ncollection_account_reports.action_report_expense_analysis'

    def _nc_analysis_buckets(self):
        return [('cogs', self.env._("Cost of Goods Sold")),
                ('operating_expenses', self.env._("Operating Expenses"))]

    def _nc_total_key(self):
        return 'total_expenses'

    def _nc_total_label(self):
        return self.env._("TOTAL EXPENSES")
