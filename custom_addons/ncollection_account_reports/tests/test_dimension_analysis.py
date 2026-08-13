# -*- coding: utf-8 -*-
"""F2-T09: Department / Cost Centre / Profit Centre analysis.

THE ASSERTION THIS FILE EXISTS FOR is apportioning. Revenue of 10,000 split
60/40 across two cost centres is 6,000 and 4,000 — never 10,000 twice. Grouping
``account.move.line`` by a distribution key would double the company's revenue,
and only on tenants that actually use splits, so the fixture splits deliberately
and both halves are checked.

Every expected figure is worked out by hand in the comment beside it, never read
back from the code under test.
"""
from datetime import date

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import AccessError
from odoo.tests import tagged


@tagged('post_install', '-at_install')
class TestDimensionAnalysis(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.CC = cls.env['ncollection.account.report.cost.center']
        cls.PC = cls.env['ncollection.account.report.profit.center']
        cls.DEP = cls.env['ncollection.account.report.department']
        cls.revenue = cls.company_data['default_account_revenue']
        cls.receivable = cls.company_data['default_account_receivable']
        cls.payable = cls.company_data['default_account_payable']
        cls.journal = cls.company_data['default_journal_misc']
        cls.cogs = cls.env['account.account'].create({
            'name': 'Cost of Sales', 'code': 'F2T9CG',
            'account_type': 'expense_direct_cost',
            'company_ids': [(6, 0, cls.env.company.ids)]})
        cls.opex = cls.company_data['default_account_expense']

        plan = cls.env.ref(
            'ncollection_account_analytics.analytic_plan_cost_center')
        cls.north = cls.env['account.analytic.account'].create({
            'name': 'North', 'plan_id': plan.id})
        cls.south = cls.env['account.analytic.account'].create({
            'name': 'South', 'plan_id': plan.id})
        split = {str(cls.north.id): 60.0, str(cls.south.id): 40.0}

        def entry(day, debit, credit, amount, distributed_on=None):
            """`distributed_on` picks which leg carries the split."""
            lines = []
            for account, dr, cr in ((debit, amount, 0.0), (credit, 0.0, amount)):
                values = {'account_id': account.id, 'debit': dr, 'credit': cr}
                if distributed_on is not None and account == distributed_on:
                    values['analytic_distribution'] = split
                lines.append((0, 0, values))
            move = cls.env['account.move'].create({
                'move_type': 'entry', 'journal_id': cls.journal.id,
                'date': day, 'line_ids': lines})
            move.action_post()
            return move

        # --- the fixture, split 60/40 throughout --------------------------
        # revenue 10,000 -> North 6,000 / South 4,000
        entry(date(2026, 3, 10), cls.receivable, cls.revenue, 10000.0,
              distributed_on=cls.revenue)
        # cost of sales 4,000 -> North 2,400 / South 1,600
        entry(date(2026, 3, 12), cls.cogs, cls.payable, 4000.0,
              distributed_on=cls.cogs)
        # operating expense 1,000 -> North 600 / South 400
        entry(date(2026, 3, 14), cls.opex, cls.payable, 1000.0,
              distributed_on=cls.opex)

        cls.date_from = date(2026, 1, 1)
        cls.date_to = date(2026, 12, 31)

    def _run(self, model, **overrides):
        values = {'date_from': self.date_from, 'date_to': self.date_to,
                  'target_move': 'posted', 'comparison_type': 'none'}
        values.update(overrides)
        return model.create(values)

    # ---- apportioning ----------------------------------------------------

    def test_a_split_is_apportioned_not_double_counted(self):
        """THE assertion. 10,000 revenue split 60/40 is 6,000 and 4,000.

        Grouping move lines by a distribution key would report 10,000 against
        both cost centres and double the company's revenue.
        """
        figures = self._run(self.CC)._nc_totals()
        self.assertAlmostEqual(figures[self.north.id]['revenue'], 6000.0, places=2)
        self.assertAlmostEqual(figures[self.south.id]['revenue'], 4000.0, places=2)
        self.assertAlmostEqual(
            figures[self.north.id]['revenue'] + figures[self.south.id]['revenue'],
            10000.0, places=2,
            msg="the dimension totals do not add up to the revenue posted")

    def test_costs_are_separated_from_revenue_by_account_type(self):
        """One dimension row splits revenue from cost because the analytic line
        carries `general_account_id`. North: 2,400 COGS + 600 opex = 3,000."""
        north = self._run(self.CC)._nc_totals()[self.north.id]
        self.assertAlmostEqual(north['cogs'], 2400.0, places=2)
        self.assertAlmostEqual(north['opex'], 600.0, places=2)
        self.assertAlmostEqual(north['expense'], 3000.0, places=2)

    def test_costs_are_presented_positive(self):
        """`amount` is credit-positive, so costs arrive negative. A report
        reading "Expense -3,000" would be arithmetically true and unreadable."""
        north = self._run(self.CC)._nc_totals()[self.north.id]
        for key in ('cogs', 'opex', 'expense'):
            self.assertGreater(north[key], 0.0, "%s rendered negative" % key)

    # ---- the metrics FPA specifies --------------------------------------

    def test_cost_centre_profit_and_margin(self):
        """North: 6,000 revenue - 3,000 expense = 3,000 profit, 50% margin.
        South: 4,000 - (1,600 + 400) = 2,000 profit, 50% margin."""
        figures = self._run(self.CC)._nc_totals()
        self.assertAlmostEqual(figures[self.north.id]['profit'], 3000.0, places=2)
        self.assertAlmostEqual(figures[self.north.id]['margin'], 50.0, places=2)
        self.assertAlmostEqual(figures[self.south.id]['profit'], 2000.0, places=2)
        self.assertAlmostEqual(figures[self.south.id]['margin'], 50.0, places=2)

    def test_profit_centre_gross_profit_excludes_operating_expenses(self):
        """FPA §Profit Center Analysis separates Gross Profit from Net Profit.
        North gross = 6,000 - 2,400 = 3,600; net = 3,000. Two reports using
        "profit" for different figures is exactly why both are asserted."""
        north = self._run(self.CC)._nc_totals()[self.north.id]
        self.assertAlmostEqual(north['gross_profit'], 3600.0, places=2)
        self.assertAlmostEqual(north['profit'], 3000.0, places=2)
        self.assertNotAlmostEqual(north['gross_profit'], north['profit'],
                                  places=2)

    def test_margin_is_none_without_revenue_not_zero(self):
        """A margin on no revenue is undefined; 0% reads as "sold at cost"."""
        plan = self.env.ref(
            'ncollection_account_analytics.analytic_plan_cost_center')
        idle = self.env['account.analytic.account'].create({
            'name': 'Idle', 'plan_id': plan.id})
        self.env['account.move'].create({
            'move_type': 'entry', 'journal_id': self.journal.id,
            'date': date(2026, 3, 20),
            'line_ids': [
                (0, 0, {'account_id': self.opex.id, 'debit': 500.0,
                        'credit': 0.0,
                        'analytic_distribution': {str(idle.id): 100.0}}),
                (0, 0, {'account_id': self.payable.id, 'debit': 0.0,
                        'credit': 500.0}),
            ]}).action_post()
        self.assertIsNone(self._run(self.CC)._nc_totals()[idle.id]['margin'])

    # ---- rendering -------------------------------------------------------

    def test_rows_carry_the_dimension_then_its_metrics(self):
        rows = self._run(self.CC)._nc_compute_lines()
        labels = [row['label'] for row in rows]
        self.assertIn('North', labels)
        self.assertIn('TOTAL', labels)
        north = next(r for r in rows if r['label'] == 'North')
        self.assertEqual(north['level'], 1)
        self.assertAlmostEqual(north['current_amount'], 3000.0, places=2)
        self.assertAlmostEqual(north['ratio_pct'], 50.0, places=2)
        self.assertEqual(north['account_id'], self.north.id)

    def test_the_total_row_is_the_sum_of_the_dimension_rows(self):
        """3,000 + 2,000 = 5,000. A total that disagrees with the rows above it
        is the failure a reader notices last."""
        rows = self._run(self.CC)._nc_compute_lines()
        dimension_rows = [r for r in rows if r['level'] == 1]
        total = next(r for r in rows if r['label'] == 'TOTAL')
        self.assertAlmostEqual(
            sum(r['current_amount'] for r in dimension_rows),
            total['current_amount'], places=2)
        self.assertAlmostEqual(total['current_amount'], 5000.0, places=2)

    def test_a_dimension_with_no_accounts_renders_only_a_total(self):
        """Departments is seeded but unused here — an empty report, not a
        crash, and not a row of zeros pretending to be a department."""
        rows = self._run(self.DEP)._nc_compute_lines()
        self.assertEqual([r['label'] for r in rows], ['TOTAL'])

    def test_each_report_has_its_own_report_action(self):
        """Two ir.actions.report sharing a report_name render every wizard
        against the FIRST action's model — shipped once, found the hard way."""
        names = set()
        for wizard in (self._run(self.CC), self._run(self.PC), self._run(self.DEP)):
            report = self.env.ref(wizard._nc_report_action_ref())
            self.assertEqual(report.model, wizard._name)
            names.add(report.report_name)
        self.assertEqual(len(names), 3, names)

    def test_pdf_and_xlsx_render(self):
        import base64
        for wizard in (self._run(self.CC), self._run(self.PC), self._run(self.DEP)):
            with self.subTest(report=wizard._name):
                self.assertEqual(
                    wizard.action_export_pdf()['type'], 'ir.actions.report')
                self.assertTrue(
                    base64.b64decode(wizard._nc_build_xlsx()).startswith(b'PK'))

    def test_a_user_without_the_accounting_group_is_denied(self):
        user = self.env['res.users'].create({
            'name': 'No Accounting', 'login': 'f2t09_noacc',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        with self.assertRaises(AccessError):
            self.CC.with_user(user).create({
                'date_from': self.date_from, 'date_to': self.date_to,
                'target_move': 'posted'})
