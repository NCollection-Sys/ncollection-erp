# -*- coding: utf-8 -*-
"""F2-T03: Native Profit & Loss.

Profitability over a period (FPA §Profit & Loss): Revenue, Cost of Sales, Gross
Profit, Operating Expenses, Net Profit — with current-vs-previous comparison
columns. Built on the F2-T01 engine + the F2-T03 comparison mixin; reads
``account.move.line`` only (FPA §4/§6 — Odoo owns the numbers).

**Replaces** ``ncollection_mis_templates``' Profit & Loss (`mis_report_pl`); the
groupings are a 1:1 transcription of that template's KPI expressions so both
produce identical figures during the transition. Retirement is #117 [F2-T07].

A P&L is a FLOW: it uses period movement (`balp` / ``_nc_period_balances``), not
the cumulative position a Balance Sheet uses.
"""
from odoo import models

# ---------------------------------------------------------------------------
# Parity map — transcribed 1:1 from
#   ncollection_mis_templates/data/mis_report_profit_and_loss.xml
# `sign`: -1 for income (credit-normal, negated to present positive revenue);
# +1 for expenses (debit-normal, presented as-is).
# tests/test_bs_pl.py holds an INDEPENDENT transcription and asserts agreement.
# ---------------------------------------------------------------------------
_PL_BUCKETS = [
    ('revenue', "Sales Revenue", -1, ('income',)),
    ('other_income', "Other Operating Income", -1, ('income_other',)),
    ('cogs', "Cost of Goods Sold", 1, ('expense_direct_cost',)),
    ('operating_expenses', "Operating Expenses", 1,
     ('expense', 'expense_depreciation')),
]

# Presentation order: (label, level, bucket_key or None for a derived subtotal).
_PL_LAYOUT = [
    ("INCOME", 0, None),
    ("Sales Revenue", 1, 'revenue'),
    ("Other Operating Income", 1, 'other_income'),
    ("TOTAL INCOME", 0, 'total_income'),
    ("COST OF SALES", 0, None),
    ("Cost of Goods Sold", 1, 'cogs'),
    ("GROSS PROFIT", 0, 'gross_profit'),
    ("OPERATING EXPENSES", 0, None),
    ("Operating Expenses", 1, 'operating_expenses'),
    ("NET PROFIT", 0, 'net_profit'),
]


class NcollectionProfitAndLoss(models.TransientModel):
    _name = 'ncollection.account.report.profit.loss'
    # Comparison mixin first — it supplies _nc_columns()/_nc_list_view_ref().
    _inherit = ['ncollection.account.report.comparison', 'ncollection.account.report']
    _description = 'NCollection Profit and Loss'

    def _nc_report_title(self):
        return self.env._("Profit & Loss")

    def _nc_label_column(self):
        # FPA §Profit & Loss names the first column "Category", not "Account".
        return self.env._("Category")

    def _nc_report_action_ref(self):
        return 'ncollection_account_reports.action_report_profit_loss'

    # ---- computation -----------------------------------------------------

    def _nc_pl_figures(self, record):
        """``(figures, details)`` for one period.

        ``figures`` holds every leaf bucket PLUS the derived subtotals, so the
        arithmetic (Gross = Income − CoS, Net = Gross − OpEx) lives in exactly
        one place and the rendered rows just read it.
        """
        balances = record._nc_period_balances()
        accounts = self.env['account.account'].with_context(
            active_test=False).browse(list(balances))
        figures, details = {}, {}
        for key, _label, sign, types in _PL_BUCKETS:
            rows = [(a, balances[a.id] * sign)
                    for a in accounts if a.account_type in types]
            figures[key] = sum(balance for _a, balance in rows)
            details[key] = sorted(rows, key=lambda r: r[0].code or '')
        figures['total_income'] = figures['revenue'] + figures['other_income']
        figures['gross_profit'] = figures['total_income'] - figures['cogs']
        figures['net_profit'] = figures['gross_profit'] - figures['operating_expenses']
        return figures, details

    def _nc_compute_lines(self):
        self.ensure_one()
        current, detail = self._nc_pl_figures(self)
        comparison = self._nc_comparison_record()
        # Computed ONCE — the comparison period costs one extra aggregate
        # query for the whole report, not one per rendered row.
        previous, previous_detail = (
            self._nc_pl_figures(comparison) if comparison is not None else ({}, {}))
        previous_by_account = {
            a.id: balance
            for rows in previous_detail.values() for a, balance in rows}

        rows = []
        for label, level, key in _PL_LAYOUT:
            if key is None:                      # section header
                rows.append(self._nc_comparison_row(label, 0.0, 0.0, level=level))
                continue
            rows.append(self._nc_comparison_row(
                label, current.get(key, 0.0), previous.get(key, 0.0), level=level))
            # Per-account detail under the leaf buckets only (mis'
            # auto_expand_accounts) — subtotals have no accounts of their own.
            # Union of both periods: an account active only in the comparison
            # period still sits inside the Previous subtotal, so dropping its
            # row would make the visible rows fail to add up to that subtotal.
            seen = {account.id for account, _b in detail.get(key, ())}
            merged = [(account, balance,
                       previous_by_account.get(account.id, 0.0))
                      for account, balance in detail.get(key, ())]
            merged += [(account, 0.0, prior)
                       for account, prior in previous_detail.get(key, ())
                       if account.id not in seen]
            for account, balance, prior in sorted(
                    merged, key=lambda row: row[0].code or ''):
                rows.append(self._nc_comparison_row(
                    account.display_name, balance, prior,
                    level=level + 1, account_id=account.id))
        return rows

    def _nc_totals(self):
        """The headline figures as a dict, for tests and for F3 dashboards that
        want them without parsing rendered rows.

        Runs its own aggregate — it does NOT reuse a previous
        ``_nc_compute_lines()`` result. Today no caller does both on one record;
        a caller that starts to (F2-T08 / F3) should hold the result rather than
        call this per KPI.
        """
        self.ensure_one()
        return self._nc_pl_figures(self)[0]
