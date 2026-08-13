# -*- coding: utf-8 -*-
"""F4-T02: the Budget vs Actual report, on the F2 engine.

FPA specifies it exactly — Filters: Fiscal Year · Budget · Department ·
Cost Center; Columns: Budget · Actual · Variance · Variance %; drill-down
Budget Line → General Ledger; PDF/Excel export.

COLUMN MAPPING. The F2 comparison mixin computes ``variance = current -
previous``, so ``current`` is **Actual** and ``previous`` is **Budget**. That
orientation is not arbitrary: it makes an overspend on a cost account a POSITIVE
variance, which is what a budget holder means by "over". The columns are
relabelled accordingly and the report ships its own list view — reusing the
generic comparison view would print "Current"/"Previous" on screen while the PDF
said "Actual"/"Budget", and this repo has already shipped one bug that lived
entirely in the gap between two renderers (#315).

ONLY APPROVED BUDGETS COUNT, and a period with none reports as unbudgeted rather
than as on-budget — see ``budget.py``. The same distinction #120 draws when this
module is absent entirely.
"""
from odoo import fields, models


class NcollectionBudgetActualReport(models.TransientModel):
    _name = 'ncollection.account.report.budget.actual'
    # Comparison mixin first: it supplies _nc_columns()/_nc_list_view_ref(),
    # which the engine declares abstract.
    _inherit = ['ncollection.account.report.comparison',
                'ncollection.account.report']
    _description = 'NCollection Budget vs Actual'

    budget_ids = fields.Many2many(
        'ncollection.account.budget', string='Budgets',
        # EXPLICIT relation name. Odoo derives it from both model table names
        # (`sorted([a, b]) + '_rel'`), which here is 71 characters against
        # Postgres' 63-char cap — the module does not install at all, which is
        # how this was found. `ncollection_account_reports`'
        # tests/test_identifier_limits.py records F2-T08 hitting the identical
        # wall; that guard is a margin ratchet for models that DO install, so it
        # cannot catch this one.
        relation='nc_budget_actual_budget_rel',
        column1='wizard_id', column2='budget_id',
        domain="[('state', '=', 'approved')]",
        help="Leave empty to include every approved budget covering the period.")

    def _nc_report_title(self):
        return self.env._("Budget vs Actual")

    def _nc_label_column(self):
        return self.env._("Account")

    def _nc_report_action_ref(self):
        return 'ncollection_account_budget.action_report_budget_actual'

    def _nc_list_view_ref(self):
        return 'ncollection_account_budget.view_report_line_budget_list'

    def _nc_columns(self):
        """FPA's four columns, in FPA's order.

        The mixin's keys are kept — the engine, the PDF and the XLSX all read
        them — and only the LABELS change, so one report cannot end up named
        differently in three renderers.
        """
        return [
            {'key': 'label', 'label': self._nc_label_column(), 'type': 'char'},
            {'key': 'previous_amount', 'label': self.env._("Budget"),
             'type': 'monetary'},
            {'key': 'current_amount', 'label': self.env._("Actual"),
             'type': 'monetary'},
            {'key': 'variance', 'label': self.env._("Variance"),
             'type': 'monetary'},
            {'key': 'variance_pct', 'label': self.env._("Variance %"),
             'type': 'percent'},
        ]

    # ---- figures ---------------------------------------------------------

    def _nc_budgeted(self):
        """``{account_id: budgeted}`` for this run's window and filter."""
        self.ensure_one()
        Budget = self.env['ncollection.account.budget']
        if not self.budget_ids:
            return Budget._nc_budgeted(self.date_from, self.date_to)
        # An explicit selection still honours the pro-rata rule and still
        # ignores anything not approved — a user picking a draft in the domain
        # is not a reason to report it.
        budgeted = {}
        for budget in self.budget_ids.filtered(lambda b: b.state == 'approved'):
            ratio = Budget._nc_overlap_ratio(
                budget.date_from, budget.date_to, self.date_from, self.date_to)
            if not ratio:
                continue
            for line in budget.line_ids:
                budgeted[line.account_id.id] = (
                    budgeted.get(line.account_id.id, 0.0) + line.amount * ratio)
        return budgeted

    def _nc_compute_lines(self):
        """One row per budgeted account, then a total.

        Accounts appear because they were BUDGETED. An account with actuals and
        no budget is deliberately absent: this report answers "did we stick to
        the plan", and an unplanned account has no plan to compare against.
        `_nc_totals` exposes the same figures for a caller that wants them.
        """
        self.ensure_one()
        budgeted = self._nc_budgeted()
        if not budgeted:
            return []
        actual = self.env['ncollection.account.budget.line']._nc_actuals(
            list(budgeted), self.date_from, self.date_to)
        accounts = self.env['account.account'].with_context(
            active_test=False).browse(list(budgeted))

        rows = []
        total_budget = total_actual = 0.0
        for account in sorted(accounts, key=lambda a: a.code or ''):
            budget_amount = budgeted.get(account.id, 0.0)
            actual_amount = actual.get(account.id, 0.0)
            total_budget += budget_amount
            total_actual += actual_amount
            rows.append(self._nc_comparison_row(
                account.display_name, actual_amount, budget_amount,
                level=1, account_id=account.id))
        rows.append(self._nc_comparison_row(
            self.env._("TOTAL"), total_actual, total_budget, level=0))
        return rows

    def _nc_totals(self):
        """``(budget, actual, variance)`` — the service surface, for tests and
        for F3 dashboards that want the headline without parsing rows."""
        self.ensure_one()
        budgeted = self._nc_budgeted()
        actual = self.env['ncollection.account.budget.line']._nc_actuals(
            list(budgeted) or [0], self.date_from, self.date_to)
        budget_total = sum(budgeted.values())
        actual_total = sum(actual.get(account_id, 0.0) for account_id in budgeted)
        return budget_total, actual_total, actual_total - budget_total
