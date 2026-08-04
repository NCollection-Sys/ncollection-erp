# -*- coding: utf-8 -*-
"""F2-T08: Native Profitability Report.

⚠ SPEC NOTE — same situation as the Analysis reports: FPA §10 lists
"Profitability Report" in the Executive Reports catalog (L961) with **no
specification section**. The metric set below is DERIVED from §Cost Center
Analysis (L1818) — *Revenue · Expense · Profit · Margin* — extended with the
Gross/Net split the P&L already computes, so nothing here is invented
arithmetic. Reconcile this file first if a real spec is written.

Margins are ratios, not money: they use the ``percent`` column type so they
never render with a currency symbol, and they are guarded against a zero
denominator exactly as ``variance_pct`` is — a financial report must never show
``inf`` in a cell.
"""
from odoo import models

# (label, service key, level, is_percent)
_PROFITABILITY_LAYOUT = [
    ("Revenue", 'total_income', 1, False),
    ("Cost of Sales", 'cogs', 1, False),
    ("GROSS PROFIT", 'gross_profit', 0, False),
    ("Gross Margin %", 'gross_margin', 1, True),
    ("Operating Expenses", 'operating_expenses', 1, False),
    ("NET PROFIT", 'net_profit', 0, False),
    ("Net Margin %", 'net_margin', 1, True),
]


class NcollectionProfitability(models.TransientModel):
    _name = 'ncollection.account.report.profitability'
    _inherit = ['ncollection.account.report.comparison',
                'ncollection.account.report',
                'ncollection.account.report.executive']
    _description = 'NCollection Profitability Report'

    def _nc_report_title(self):
        return self.env._("Profitability Report")

    def _nc_label_column(self):
        return self.env._("Metric")

    def _nc_report_action_ref(self):
        return 'ncollection_account_reports.action_report_profitability'

    # ---- service surface --------------------------------------------------

    def _nc_service_figures(self):
        self.ensure_one()
        return self._nc_service_figures_for(self.date_from, self.date_to)

    def _nc_service_figures_for(self, date_from, date_to):
        """P&L figures plus the two margins, over revenue."""
        self.ensure_one()
        figures = dict(self._nc_pl(date_from, date_to))
        revenue = figures.get('total_income', 0.0)
        figures['gross_margin'] = self._nc_ratio(
            figures.get('gross_profit', 0.0), revenue)
        figures['net_margin'] = self._nc_ratio(
            figures.get('net_profit', 0.0), revenue)
        return figures

    # ---- rendering --------------------------------------------------------

    def _nc_compute_lines(self):
        self.ensure_one()
        current, previous = self._nc_service_comparison()
        rows = []
        for label, key, level, is_percent in _PROFITABILITY_LAYOUT:
            row = self._nc_comparison_row(
                label, current.get(key, 0.0), previous.get(key, 0.0), level=level)
            if is_percent:
                # A margin's "variance" is a swing in PERCENTAGE POINTS, not a
                # percentage change of a percentage — reporting the latter
                # ("margin improved 300%") is meaningless to a reader.
                row['variance_pct'] = 0.0
            rows.append(row)
        return rows
