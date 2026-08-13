# -*- coding: utf-8 -*-
"""F2-T09: Department / Cost Centre / Profit Centre analysis.

The three reports #118 [F2-T08] deliberately did not build, because the
dimension to group by did not exist. #120 [F4-T01] created it — three root
``account.analytic.plan`` records in ``ncollection_account_analytics`` — so this
is the reporting half, which FPA §7 says must live here and never there
(``Must Never Own: Reports``).

WHY THIS READS ``account.analytic.line`` AND NOT ``account.move.line``.
``analytic_distribution`` is a percentage split: a 1,000 journal item shared
60/40 between two cost centres belongs 600 to one and 400 to the other. Odoo
writes exactly that into ``account.analytic.line.amount`` — already apportioned
— whereas grouping move lines by a distribution key would report 1,000 against
both and double the company's revenue. The error only appears on tenants that
actually use splits, which is the silent kind, so the fixtures here split
deliberately.

``general_account_id`` is a STORED Many2one to ``account.account`` on the
analytic line, which is what lets one dimension row separate revenue from cost:
the figures below are three aggregates over the same lines, differing only in
which account types they admit.

SIGN. ``amount`` is credit-positive (Odoo writes ``-balance * pct / 100``), so
revenue arrives positive and costs negative. Costs are negated on the way out so
a reader sees "Expense 10,000" rather than "-10,000" — the same presentation
choice the P&L makes with ``sign``.

AN UNDEFINED MARGIN DOES NOT SURVIVE RENDERING, AND THIS FILE USED TO CLAIM IT
DID. A cost centre with costs and no revenue has an undefined margin, and
``_nc_dimension_figures`` returns ``None`` for it — correct, and useful to a
service consumer. But ``ncollection.account.report.line.ratio_pct`` is a
``fields.Float``, and every renderer coerces: the engine writes ``None`` into
the field (Odoo's Float does ``float(value or 0.0)``), the PDF template does
``value or 0.0``, and the XLSX writer does the same. So the on-screen list, the
PDF and the workbook all print **0.00%** — indistinguishable from a genuine
break-even centre, which is exactly the misreading the sentinel was for.

A Float column cannot express "undefined", so the honest options were to add a
nullable presentation or to say so. This says so, and
``test_an_undefined_margin_renders_as_zero_which_is_a_known_limitation`` pins
the real behaviour so nobody re-derives the false guarantee from the comment.
Filed for a proper fix.

TARGET_MOVE DOES NOT APPLY HERE, so the three wizard forms do not offer it.
Odoo creates ``account.analytic.line`` only for POSTED move lines (and
``button_draft`` unlinks them again when a move is reset), so "All Entries"
could only ever return the same rows as "Posted Entries". Showing a control
that cannot change the answer is worse than omitting it.

METRICS ARE FPA'S, WHERE FPA HAS THEM. §Cost Center Analysis specifies Revenue ·
Expense · Profit · Margin; §Profit Center Analysis specifies Revenue · Expenses ·
Gross Profit · Net Profit. **Department Analysis has no specification** — it is a
catalog bullet with no metrics — so it follows the Cost Centre shape, and this
sentence is where that choice is recorded rather than left to be inferred.
"""
from odoo import models

REVENUE_TYPES = ('income', 'income_other')
COGS_TYPES = ('expense_direct_cost',)
OPEX_TYPES = ('expense', 'expense_other', 'expense_depreciation')


class NcollectionDimensionAnalysisBase(models.AbstractModel):
    """One row per analytic account, its metrics beneath it, a grand total."""

    _name = 'ncollection.account.report.dimension.base'
    _description = 'NCollection Analytic Dimension Analysis Base'

    # ---- what a concrete report must say ---------------------------------

    def _nc_dimension(self):
        """``'department'`` | ``'cost_center'`` | ``'profit_center'``."""
        raise NotImplementedError

    def _nc_metric_layout(self):
        """``[(figure key, label)]`` shown beneath each dimension row."""
        return [('revenue', self.env._("Revenue")),
                ('expense', self.env._("Expense"))]

    def _nc_headline_key(self):
        """The figure key shown ON the dimension row itself."""
        return 'profit'

    def _nc_label_column(self):
        return self.env._("Cost Centre")

    # ---- columns ---------------------------------------------------------

    def _nc_columns(self):
        """The comparison columns plus Margin, which FPA lists as a metric."""
        return super()._nc_columns() + [
            {'key': 'ratio_pct', 'label': self.env._("Margin %"),
             'type': 'percent'}]

    def _nc_list_view_ref(self):
        # Its own view, not Profitability's: that one labels `ratio_pct`
        # "% of Revenue" — correct there, where every row IS a share of
        # revenue. Here the figure is a MARGIN, and one report whose column is
        # named one thing on screen and another in the PDF is the kind of gap
        # between layers that already produced one bug in this ticket.
        return 'ncollection_account_reports.view_report_line_dimension_list'

    # ---- figures ---------------------------------------------------------

    def _nc_analytic_window(self, date_from, date_to):
        """The window + company, without any account-type restriction."""
        self.ensure_one()
        return [('date', '>=', date_from), ('date', '<=', date_to),
                ('company_id', '=', self.company_id.id)]

    def _nc_analytic_domain(self, date_from, date_to, account_types):
        return self._nc_analytic_window(date_from, date_to) + [
            ('general_account_id.account_type', 'in', list(account_types))]

    def _nc_unclassified_domain(self, date_from, date_to):
        """Analytic lines carrying NO financial account.

        ``general_account_id`` is computed from ``move_line_id`` and is NOT
        required: a line entered by hand through Analytic Items, or one from an
        uninvoiced timesheet, has none. Such a line matches none of the three
        type domains, so without this it would drop out of revenue AND cost and
        the dimension would read 0/0/0 while carrying real recorded activity —
        absent rather than misclassified, which is the harder kind to notice.

        It is surfaced as its own figure rather than folded into a bucket:
        this module cannot know whether an unclassified amount is income or
        cost, and guessing would be worse than showing it.
        """
        return self._nc_analytic_window(date_from, date_to) + [
            ('general_account_id', '=', False)]

    def _nc_dimension_figures(self, date_from, date_to):
        """``{analytic_account: {revenue, cogs, opex, expense, profit,
        gross_profit, margin}}`` for the window.

        Three aggregates over the same analytic lines, differing only in which
        account types they admit — that is what ``general_account_id`` buys.
        Returns ``{}`` when the dimension's plan is absent, which propagates
        "this tenant cannot answer that" rather than "no activity".
        """
        self.ensure_one()
        Dimension = self.env['ncollection.account.analytics.dimension']
        plan = Dimension._nc_plan(self._nc_dimension())
        if not plan:
            return {}
        accounts = self.env['account.analytic.account'].search(
            [('plan_id', 'child_of', plan.id)])
        if not accounts:
            return {}
        # Per-plan column, never a hardcoded `account_id` — Odoo stores the
        # account in `x_plan<id>_id` for every plan but the project one, so a
        # hardcoded field silently returns nothing (#120).
        column = plan._column_name()
        base = [(column, 'in', accounts.ids)]

        def totals(account_types):
            rows = self.env['account.analytic.line']._read_group(
                base + self._nc_analytic_domain(date_from, date_to, account_types),
                groupby=[column], aggregates=['amount:sum'])
            return {account.id: amount or 0.0 for account, amount in rows
                    if account}

        revenue = totals(REVENUE_TYPES)
        cogs = totals(COGS_TYPES)
        opex = totals(OPEX_TYPES)
        unclassified_rows = self.env['account.analytic.line']._read_group(
            base + self._nc_unclassified_domain(date_from, date_to),
            groupby=[column], aggregates=['amount:sum'])
        unclassified = {account.id: amount or 0.0
                        for account, amount in unclassified_rows if account}

        figures = {}
        for account in accounts:
            # Costs arrive negative (amount is credit-positive); negate so the
            # report reads "Expense 10,000", not "-10,000".
            account_revenue = revenue.get(account.id, 0.0)
            account_cogs = -cogs.get(account.id, 0.0)
            account_opex = -opex.get(account.id, 0.0)
            expense = account_cogs + account_opex
            profit = account_revenue - expense
            figures[account] = {
                'revenue': account_revenue,
                'cogs': account_cogs,
                'opex': account_opex,
                'expense': expense,
                'gross_profit': account_revenue - account_cogs,
                'profit': profit,
                # Real activity this module cannot classify. Surfaced so it
                # cannot vanish; deliberately NOT added to profit, because its
                # sign has no agreed meaning here.
                'unclassified': unclassified.get(account.id, 0.0),
                # None at the SERVICE layer, where a consumer can act on it.
                # See the module docstring: it does NOT survive rendering, and
                # pretending otherwise was this file's own bug.
                'margin': (profit / account_revenue * 100.0
                           if account_revenue else None),
            }
        return figures

    # ---- rendering -------------------------------------------------------

    def _nc_compute_lines(self):
        """One row per analytic account, its metrics beneath, then the total.

        Rows span the UNION of both periods: a cost centre that earned nothing
        this period but did last one still belongs on a comparison report, and
        dropping it would make the visible rows fail to add up to the total —
        the F2-T03 lesson, applied here.
        """
        self.ensure_one()
        current = self._nc_dimension_figures(self.date_from, self.date_to)
        window = self._nc_comparison_range()
        previous = self._nc_dimension_figures(*window) if window else {}

        headline = self._nc_headline_key()
        rows = []
        totals = {'current': 0.0, 'previous': 0.0, 'revenue': 0.0}
        for account in sorted(set(current) | set(previous),
                              key=lambda a: a.display_name):
            now = current.get(account, {})
            before = previous.get(account, {})
            # account_id is NOT set. It is a Many2one to `account.account`
            # (the FINANCIAL account) on ncollection.account.report.line, and
            # `account` here is an `account.analytic.account` — a different
            # table. Passing it wrote an analytic id into a financial-account
            # foreign key, which the full test matrix rejected outright and
            # which, wherever the two id sequences happen to COLLIDE, would
            # silently open some unrelated account's journal items from the
            # drill-down button. FPA specifies no Drill Down for these reports
            # (unlike §Balance Sheet, which has one), so there is nothing to
            # lose; an analytic-aware drill-down would need its own field on
            # the line model.
            row = self._nc_comparison_row(
                account.display_name, now.get(headline, 0.0),
                before.get(headline, 0.0), level=1)
            row['ratio_pct'] = now.get('margin')
            rows.append(row)
            totals['current'] += now.get(headline, 0.0)
            totals['previous'] += before.get(headline, 0.0)
            totals['revenue'] += now.get('revenue', 0.0)
            for key, label in self._nc_metric_layout():
                rows.append(self._nc_comparison_row(
                    label, now.get(key, 0.0), before.get(key, 0.0), level=2))

        total = self._nc_comparison_row(
            self.env._("TOTAL"), totals['current'], totals['previous'], level=0)
        total['ratio_pct'] = (totals['current'] / totals['revenue'] * 100.0
                              if totals['revenue'] else None)
        rows.append(total)
        return rows

    def _nc_totals(self):
        """``{analytic_account_id: figures}`` — the service surface, for tests
        and for F3 dashboards that want the numbers without parsing rows."""
        self.ensure_one()
        return {account.id: figures for account, figures
                in self._nc_dimension_figures(self.date_from, self.date_to).items()}


class NcollectionCostCenterAnalysis(models.TransientModel):
    """FPA §Cost Center Analysis: Revenue · Expense · Profit · Margin."""

    _name = 'ncollection.account.report.cost.center'
    _inherit = ['ncollection.account.report.dimension.base',
                'ncollection.account.report.comparison',
                'ncollection.account.report']
    _description = 'NCollection Cost Center Analysis'

    def _nc_dimension(self):
        return 'cost_center'

    def _nc_report_title(self):
        return self.env._("Cost Center Analysis")

    def _nc_report_action_ref(self):
        return 'ncollection_account_reports.action_report_cost_center'


class NcollectionProfitCenterAnalysis(models.TransientModel):
    """FPA §Profit Center Analysis: Revenue · Expenses · Gross Profit · Net
    Profit. Gross profit is revenue less COST OF SALES only; net profit is
    revenue less every expense. Two reports using "profit" for different
    figures is exactly the confusion this spells out."""

    _name = 'ncollection.account.report.profit.center'
    _inherit = ['ncollection.account.report.dimension.base',
                'ncollection.account.report.comparison',
                'ncollection.account.report']
    _description = 'NCollection Profit Center Analysis'

    def _nc_dimension(self):
        return 'profit_center'

    def _nc_label_column(self):
        return self.env._("Profit Centre")

    def _nc_metric_layout(self):
        return [('revenue', self.env._("Revenue")),
                ('cogs', self.env._("Cost of Sales")),
                ('gross_profit', self.env._("Gross Profit")),
                ('opex', self.env._("Operating Expenses"))]

    def _nc_report_title(self):
        return self.env._("Profit Center Analysis")

    def _nc_report_action_ref(self):
        return 'ncollection_account_reports.action_report_profit_center'


class NcollectionDepartmentAnalysis(models.TransientModel):
    """FPA lists Department Analysis with NO metrics, so it follows the Cost
    Centre shape. That is a choice, not a specification — recorded here and in
    the module docstring rather than left for a reader to reverse-engineer."""

    _name = 'ncollection.account.report.department'
    _inherit = ['ncollection.account.report.dimension.base',
                'ncollection.account.report.comparison',
                'ncollection.account.report']
    _description = 'NCollection Department Analysis'

    def _nc_dimension(self):
        return 'department'

    def _nc_label_column(self):
        return self.env._("Department")

    def _nc_report_title(self):
        return self.env._("Department Analysis")

    def _nc_report_action_ref(self):
        return 'ncollection_account_reports.action_report_department'
