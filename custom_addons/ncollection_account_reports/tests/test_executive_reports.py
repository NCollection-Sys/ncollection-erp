# -*- coding: utf-8 -*-
"""F2-T08: the four FPA Executive Reports.

The headline test here is ``test_executive_reports_never_recompute_*``: the
ticket's third acceptance criterion is that consumers use these services rather
than recomputing, and the same rule is enforced one level down — the executive
layer composes F2-T03, it does not re-derive from journal items. That guard is
structural (AST over the real source), so it cannot be satisfied by a comment.
"""
import ast
import base64
import inspect
from datetime import date

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import UserError
from odoo.tests import tagged

from ..wizard import (executive_base, financial_summary, profitability,
                      revenue_expense_analysis)

_EXECUTIVE_MODULES = (executive_base, financial_summary,
                      revenue_expense_analysis, profitability)

# The eight KPIs FPA §Financial Summary (L1878-1888) mandates, by service key.
_FPA_SUMMARY_KPIS = {'revenue', 'expenses', 'net_profit', 'cash',
                     'receivables', 'payables', 'assets', 'liabilities'}


def _code_without_docstrings(module):
    """The module's executable source, docstrings and comments removed.

    A grep over raw source would trip on this layer's own prose (which names
    ``account.move.line`` precisely to say it must not be touched), so the guard
    parses instead: what remains is only code that actually runs.
    """
    tree = ast.parse(inspect.getsource(module))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef,
                                 ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = node.body
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            body.pop(0)
    return ast.unparse(tree)


@tagged('post_install', '-at_install')
class TestExecutiveReports(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Summary = cls.env['ncollection.account.report.summary']
        cls.Revenue = cls.env['ncollection.account.report.revenue']
        cls.Expense = cls.env['ncollection.account.report.expense']
        cls.Profit = cls.env['ncollection.account.report.profitability']
        cls.PL = cls.env['ncollection.account.report.profit.loss']
        cls.BS = cls.env['ncollection.account.report.balance.sheet']

        cls.receivable = cls.company_data['default_account_receivable']
        cls.revenue_account = cls.company_data['default_account_revenue']
        cls.expense_account = cls.company_data['default_account_expense']
        cls.journal = cls.company_data['default_journal_misc']

        def entry(day, debit_account, credit_account, amount):
            move = cls.env['account.move'].create({
                'move_type': 'entry', 'journal_id': cls.journal.id, 'date': day,
                'line_ids': [
                    (0, 0, {'account_id': debit_account.id, 'debit': amount,
                            'credit': 0.0}),
                    (0, 0, {'account_id': credit_account.id, 'debit': 0.0,
                            'credit': amount}),
                ]})
            move.action_post()
            return move

        # Prior year: revenue 400, expense 100 -> net 300. The prior-year
        # EXPENSE matters: without it the Expense Analysis comparison column is
        # legitimately all-zero and a comparison test passes vacuously.
        entry(date(2025, 6, 30), cls.receivable, cls.revenue_account, 400.0)
        entry(date(2025, 7, 31), cls.expense_account, cls.receivable, 100.0)
        # Current year: revenue 1000, expense 300 -> net 700.
        entry(date(2026, 6, 15), cls.receivable, cls.revenue_account, 1000.0)
        entry(date(2026, 6, 20), cls.expense_account, cls.receivable, 300.0)

        cls.date_from = date(2026, 1, 1)
        cls.date_to = date(2026, 12, 31)

    def _make(self, model, **overrides):
        values = {'date_from': self.date_from, 'date_to': self.date_to,
                  'target_move': 'posted', 'comparison_type': 'none'}
        values.update(overrides)
        return model.create(values)

    # ---- AC3: the boundary — compose, never recompute --------------------

    def test_executive_reports_never_recompute_from_journal_items(self):
        """No executive report may aggregate account.move.line itself.

        If one ever does, its figure can drift from the Balance Sheet / P&L it
        is supposed to summarise, and a dashboard reading it would disagree
        with the statement — silently. Structural guard: parsed source, so a
        comment claiming compliance cannot satisfy it.
        """
        offenders = []
        for module in _EXECUTIVE_MODULES:
            code = _code_without_docstrings(module)
            for needle in ('account.move.line', '_read_group', '_nc_filter_domain'):
                if needle in code:
                    offenders.append('%s uses %s' % (module.__name__, needle))
        self.assertEqual(
            offenders, [],
            "executive reports must compose F2-T03 services, not re-derive: %s"
            % offenders)

    def test_summary_revenue_equals_the_profit_and_loss(self):
        """Behavioural half of the boundary: the composed figure and the
        statement it came from must agree to the penny."""
        summary = self._make(self.Summary)._nc_service_figures()
        pl = self._make(self.PL)
        figures = pl._nc_pl_figures(pl)[0]
        self.assertAlmostEqual(summary['revenue'], figures['total_income'], places=2)
        self.assertAlmostEqual(summary['net_profit'], figures['net_profit'], places=2)

    def test_summary_position_equals_the_balance_sheet(self):
        summary = self._make(self.Summary)._nc_service_figures()
        assets, liabilities_equity = self._make(self.BS)._nc_totals()
        self.assertAlmostEqual(summary['assets'], assets, places=2)
        self.assertNotAlmostEqual(summary['assets'], 0.0, places=2)
        self.assertLessEqual(summary['liabilities'], liabilities_equity)

    def test_every_report_exposes_a_service_surface_for_dashboards(self):
        """#56 consumes _nc_service_figures(); every report must implement it."""
        for model in (self.Summary, self.Revenue, self.Expense, self.Profit):
            with self.subTest(report=model._name):
                figures = self._make(model)._nc_service_figures()
                self.assertIsInstance(figures, dict)
                self.assertTrue(figures)

    # ---- AC1: the FPA 8-KPI spec ----------------------------------------

    def test_financial_summary_implements_the_fpa_eight_kpis(self):
        figures = self._make(self.Summary)._nc_service_figures()
        self.assertEqual(_FPA_SUMMARY_KPIS - set(figures), set())

    def test_summary_revenue_minus_expenses_reconciles_to_net_profit(self):
        """The first thing an executive checks. If Revenue is only the sales
        bucket rather than total income, these stop adding up."""
        f = self._make(self.Summary)._nc_service_figures()
        self.assertAlmostEqual(f['revenue'] - f['expenses'], f['net_profit'], places=2)
        self.assertAlmostEqual(f['revenue'], 1000.0, places=2)
        self.assertAlmostEqual(f['expenses'], 300.0, places=2)
        self.assertAlmostEqual(f['net_profit'], 700.0, places=2)

    def test_summary_presents_credit_normal_kpis_as_positive(self):
        """"Payables: -40,000" reads as a negative liability, which is not what
        the number means."""
        f = self._make(self.Summary)._nc_service_figures()
        self.assertGreaterEqual(f['receivables'], 0.0)
        self.assertGreaterEqual(f['payables'], 0.0)

    # ---- AC2: analysis + profitability ----------------------------------

    def test_revenue_and_expense_analysis_totals_match_the_pl(self):
        pl = self._make(self.PL)
        figures = pl._nc_pl_figures(pl)[0]
        revenue = self._make(self.Revenue)._nc_service_figures()
        expense = self._make(self.Expense)._nc_service_figures()
        self.assertAlmostEqual(revenue['total_income'], figures['total_income'], places=2)
        self.assertAlmostEqual(
            expense['total_expenses'],
            figures['cogs'] + figures['operating_expenses'], places=2)

    def test_profitability_margins(self):
        f = self._make(self.Profit)._nc_service_figures()
        self.assertAlmostEqual(f['net_margin'], 70.0, places=2)   # 700 / 1000
        self.assertAlmostEqual(
            f['gross_margin'], f['gross_profit'] / f['total_income'] * 100.0, places=2)

    def test_margins_render_as_a_percent_column_never_as_currency(self):
        """A ratio in `current_amount` (a Monetary field, rendered through the
        monetary widget) would print "AED 70.00" against a row labelled
        "Net Margin". Margins are a percent COLUMN instead, so the Net Profit
        row's share of revenue IS the net margin."""
        wizard = self._make(self.Profit)
        rows = {r['label']: r for r in wizard._nc_compute_lines()}
        figures = wizard._nc_service_figures()
        self.assertAlmostEqual(
            rows['NET PROFIT']['ratio_pct'], figures['net_margin'], places=2)
        self.assertAlmostEqual(
            rows['GROSS PROFIT']['ratio_pct'], figures['gross_margin'], places=2)
        # No row may carry a ratio in a monetary cell.
        for label, row in rows.items():
            self.assertNotIn('margin', label.lower(), label)
        # The percent column exists and is typed as such.
        ratio = next(c for c in wizard._nc_columns() if c['key'] == 'ratio_pct')
        self.assertEqual(ratio['type'], 'percent')
        for column in wizard._nc_columns():
            if column['type'] == 'monetary':
                self.assertNotIn('pct', column['key'])

    def test_profitability_margin_is_zero_not_infinite_without_revenue(self):
        """An empty period must render 0.00%, never inf, never a traceback."""
        empty = self._make(self.Profit, date_from=date(2020, 1, 1),
                           date_to=date(2020, 12, 31))
        f = empty._nc_service_figures()
        self.assertEqual(f['net_margin'], 0.0)
        self.assertEqual(f['gross_margin'], 0.0)

    def test_comparison_columns_populate_across_all_reports(self):
        for model in (self.Summary, self.Revenue, self.Expense, self.Profit):
            with self.subTest(report=model._name):
                wizard = self._make(model, comparison_type='previous_year')
                rows = wizard._nc_compute_lines()
                self.assertTrue(any(r['previous_amount'] for r in rows),
                                "no previous-period figure rendered")

    def test_summary_previous_year_comparison(self):
        wizard = self._make(self.Summary, comparison_type='previous_year')
        rows = {r['label']: r for r in wizard._nc_compute_lines()}
        # 2026: 1000 - 300 = 700.  2025: 400 - 100 = 300.
        self.assertAlmostEqual(rows['Net Profit']['current_amount'], 700.0, places=2)
        self.assertAlmostEqual(rows['Net Profit']['previous_amount'], 300.0, places=2)
        self.assertAlmostEqual(rows['Net Profit']['variance'], 400.0, places=2)

    # ---- drill-down reconciles (the F2-T03 lesson, applied up front) -----

    def test_drill_down_matches_the_displayed_figure(self):
        """Every row exposing an account_id must drill into that same number.
        Revenue/Expense Analysis are period reports, so the engine's default
        domain is already right — pinned so it cannot silently desync."""
        for model, account, negate in ((self.Revenue, self.revenue_account, True),
                                       (self.Expense, self.expense_account, False)):
            with self.subTest(report=model._name):
                wizard = self._make(model)
                row = next(r for r in wizard._nc_compute_lines()
                           if r['account_id'] == account.id)
                domain = wizard._nc_move_line_domain(account=account)
                drilled = sum(self.env['account.move.line']
                              .search(domain).mapped('balance'))
                expected = -drilled if negate else drilled
                self.assertAlmostEqual(row['current_amount'], expected, places=2)

    # ---- company scoping through the composition layer --------------------

    def test_executive_figures_exclude_another_companys_postings(self):
        """The composition layer hands filters to a child wizard through an
        EXPLICIT field list. If ``company_id`` ever falls off that list, Odoo's
        ``new()`` silently falls back to ``env.company`` — the child would then
        aggregate whatever company the session happens to be in, while the
        Balance Sheet it claims to summarise would not. Correct today; this is
        the guard that keeps it correct.
        """
        other = self.setup_other_company()
        other_company = other['company']
        other_journal = other['default_journal_misc']
        move = self.env['account.move'].with_company(other_company).create({
            'move_type': 'entry', 'journal_id': other_journal.id,
            'date': date(2026, 6, 18),
            'line_ids': [
                (0, 0, {'account_id': other['default_account_receivable'].id,
                        'debit': 5000.0, 'credit': 0.0}),
                (0, 0, {'account_id': other['default_account_revenue'].id,
                        'debit': 0.0, 'credit': 5000.0}),
            ]})
        move.action_post()

        # Our company's figures must be untouched by the 5,000 next door.
        figures = self._make(self.Summary)._nc_service_figures()
        self.assertAlmostEqual(figures['revenue'], 1000.0, places=2)
        self.assertAlmostEqual(figures['net_profit'], 700.0, places=2)

        # ...and the other company's report must see its own 5,000, not ours.
        theirs = self.Summary.with_company(other_company).create({
            'company_id': other_company.id,
            'date_from': self.date_from, 'date_to': self.date_to,
            'target_move': 'posted', 'comparison_type': 'none',
        })._nc_service_figures()
        self.assertAlmostEqual(theirs['revenue'], 5000.0, places=2)

    def test_child_statement_inherits_every_filter_the_domain_uses(self):
        """Structural guard on the same failure: the explicit inherit list must
        cover every field the engine's filter domain actually reads."""
        from ..wizard.executive_base import _INHERITED_FILTERS
        engine_filters = {'company_id', 'date_from', 'date_to', 'target_move',
                          'journal_ids', 'account_ids', 'partner_ids'}
        self.assertEqual(engine_filters - set(_INHERITED_FILTERS), set())

    # ---- security (Rule 4) ------------------------------------------------

    def test_report_runs_are_private_to_their_creator(self):
        other = self.env['res.users'].create({
            'name': 'Exec Reader', 'login': 'nc_exec_reader',
            'group_ids': [(6, 0, [self.env.ref('account.group_account_readonly').id,
                                  self.env.ref('base.group_user').id])],
        })
        for model in (self.Summary, self.Revenue, self.Expense, self.Profit):
            with self.subTest(report=model._name):
                mine = self._make(model)
                self.assertFalse(
                    model.with_user(other).search([('id', '=', mine.id)]),
                    "another user could read someone else's report run")

    def test_drill_down_rejects_a_forged_report_model(self):
        wizard = self._make(self.Revenue)
        action = wizard.action_view()
        line = self.env['ncollection.account.report.line'].browse(
            action['domain'][0][2])[0]
        # sudo() only to ARRANGE the forged value — #318 removed perm_write, so
        # a real user cannot reach this state. The dispatch guard is tested
        # separately from the ACL on purpose; neither should prop up the other.
        line.sudo().write({'report_model': 'res.users', 'report_res_id': 1})
        with self.assertRaises(UserError):
            line.action_drill_down()

    # ---- exports ----------------------------------------------------------

    def test_each_report_has_its_own_report_action(self):
        """Prior regression in this module: a shared report_name makes Odoo
        render every wizard against the FIRST action's model."""
        names = set()
        for model in (self.Summary, self.Revenue, self.Expense, self.Profit):
            wizard = self._make(model)
            report = self.env.ref(wizard._nc_report_action_ref())
            self.assertEqual(report.model, wizard._name)
            names.add(report.report_name)
        self.assertEqual(len(names), 4, names)

    def test_pdf_and_xlsx_render_for_every_report(self):
        for model in (self.Summary, self.Revenue, self.Expense, self.Profit):
            with self.subTest(report=model._name):
                wizard = self._make(model, comparison_type='previous_year')
                self.assertEqual(
                    wizard.action_export_pdf()['type'], 'ir.actions.report')
                # #250: streamed from a controller, nothing persisted.
                xlsx = wizard.action_export_xlsx()
                self.assertIn('/ncollection/account_reports/xlsx/', xlsx['url'])
                self.assertTrue(
                    base64.b64decode(wizard._nc_build_xlsx()).startswith(b'PK'))
