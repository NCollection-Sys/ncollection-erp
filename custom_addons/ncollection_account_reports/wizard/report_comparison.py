# -*- coding: utf-8 -*-
"""F2-T03: the comparison-period mixin.

FPA §Balance Sheet and §Profit & Loss both specify the same four columns —
``Current · Previous · Variance · Variance %`` — so the period arithmetic lives
here once rather than twice.

Deliberately a SEPARATE ``AbstractModel`` rather than fields bolted onto the
F2-T01 engine: the General Ledger and Trial Balance (F2-T02, already shipped)
have no comparison concept, and giving them dormant comparison fields would be
behaviour they never asked for. Reports that want comparison inherit BOTH the
engine and this mixin; reports that don't are untouched.

F2-T08 (#118, executive reports: Revenue/Expense Analysis, Profitability) needs
exactly this arithmetic and is expected to inherit this mixin rather than
reimplement it.
"""
from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import UserError


class NcollectionAccountReportComparison(models.AbstractModel):
    _name = 'ncollection.account.report.comparison'
    _description = 'NCollection Financial Report Comparison Period'

    comparison_type = fields.Selection(
        [('none', 'No Comparison'),
         ('previous_period', 'Previous Period'),
         ('previous_year', 'Same Period Last Year'),
         ('custom', 'Custom Period')],
        default='previous_period', required=True, string='Compare With')
    comparison_date_from = fields.Date(string='Compare From')
    comparison_date_to = fields.Date(string='Compare To')

    # ---- the comparison range -------------------------------------------

    def _nc_comparison_range(self):
        """``(date_from, date_to)`` of the comparison period, or ``None`` when
        no comparison is requested.

        ``previous_period`` ends the day before the report starts and has the
        SAME length, so a 3-month report compares against the 3 months before it
        — never a fixed calendar month, which would silently compare unequal
        spans and make the variance meaningless.
        """
        self.ensure_one()
        if self.comparison_type == 'none':
            return None
        if self.comparison_type == 'custom':
            if not (self.comparison_date_from and self.comparison_date_to):
                raise UserError(self.env._(
                    "A custom comparison needs both a Compare From and a Compare To date."))
            if self.comparison_date_from > self.comparison_date_to:
                raise UserError(self.env._(
                    "The custom comparison period starts after it ends."))
            return self.comparison_date_from, self.comparison_date_to
        if self.comparison_type == 'previous_year':
            return (self.date_from - relativedelta(years=1),
                    self.date_to - relativedelta(years=1))
        # previous_period: same length, immediately before the report period.
        end = self.date_from - relativedelta(days=1)
        return end - (self.date_to - self.date_from), end

    def _nc_comparison_record(self):
        """This same wizard evaluated over the COMPARISON period, or ``None``.

        ``new(..., origin=self)`` gives an in-memory record that inherits every
        other filter (company, journals, accounts, partners, target_move) from
        the real one and overrides only the dates — so every engine helper
        (``_nc_filter_domain``, ``_nc_closing_balances``, ``_nc_period_balances``)
        computes the comparison figure through the exact same code path as the
        current one. No duplicated domain logic, nothing to drift.
        """
        self.ensure_one()
        window = self._nc_comparison_range()
        if window is None:
            return None
        return self.new(
            {'date_from': window[0], 'date_to': window[1]}, origin=self)

    # ---- variance --------------------------------------------------------

    @api.model
    def _nc_variance(self, current, previous):
        """``(variance, variance_pct)``.

        Percentage is over ``abs(previous)`` so the SIGN always reports the
        direction of the real-world move. Over a raw signed denominator, a
        credit-normal figure improving from -100 to -150 would read as a
        *positive* 50%% — the opposite of what a reader concludes.

        ``previous == 0`` yields 0.0, not infinity: there is no meaningful
        percentage change from nothing, and a report that renders ``inf`` (or
        raises) in a cell is worse than one that renders a plain dash.
        """
        variance = current - previous
        return variance, (variance / abs(previous) * 100.0) if previous else 0.0

    def _nc_comparison_row(self, label, current, previous, level=0, account_id=False):
        """One rendered row carrying all four FPA comparison columns."""
        variance, variance_pct = self._nc_variance(current, previous)
        return {
            'label': label,
            'account_id': account_id,
            'current_amount': current,
            'previous_amount': previous,
            'variance': variance,
            'variance_pct': variance_pct,
            # `balance` keeps the generic engine surfaces (drill-down list,
            # anything reading the common column) meaningful for these reports.
            'balance': current,
            'level': level,
        }

    def _nc_columns(self):
        """The FPA comparison column set, shared by every report on this mixin.
        The first column's label differs per report ("Account" vs "Category"),
        so it comes from ``_nc_label_column()``."""
        columns = [{'key': 'label', 'label': self._nc_label_column(), 'type': 'char'},
                   {'key': 'current_amount', 'label': self.env._("Current"), 'type': 'monetary'}]
        if self.comparison_type != 'none':
            columns += [
                {'key': 'previous_amount', 'label': self.env._("Previous"), 'type': 'monetary'},
                {'key': 'variance', 'label': self.env._("Variance"), 'type': 'monetary'},
                {'key': 'variance_pct', 'label': self.env._("Variance %"), 'type': 'percent'},
            ]
        return columns

    def _nc_label_column(self):
        return self.env._("Account")

    def _nc_list_view_ref(self):
        return 'ncollection_account_reports.view_report_line_comparison_list'
