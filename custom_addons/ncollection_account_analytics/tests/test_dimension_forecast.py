# -*- coding: utf-8 -*-
"""F4-T01: the analytic dimensions, budget variance, and the trend.

THE ASSERTION THAT MATTERS for the dimension is the distribution split. A
journal item of 1,000 shared 60/40 across two cost centres must contribute 600
and 400 — not 1,000 twice. Summing ``account.move.line.balance`` grouped by a
distribution key would double-count every split item, and that error only shows
up on tenants that actually use distributions, which is the silent kind. So the
fixture below deliberately splits one entry and checks both halves.

For variance, the assertion that matters is the ABSENCE case: with #121 not
installed there is no budget, and the result must say so rather than return
0.00, which reads as "exactly on budget" — the most dangerous number this
module could produce.
"""
from datetime import date

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.tests import tagged


@tagged('post_install', '-at_install')
class TestDimensionAndForecast(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Dim = cls.env['ncollection.account.analytics.dimension']
        cls.Forecast = cls.env['ncollection.account.analytics.forecast']
        cls.revenue = cls.company_data['default_account_revenue']
        cls.receivable = cls.company_data['default_account_receivable']
        cls.journal = cls.company_data['default_journal_misc']

        cls.cc_plan = cls.env.ref(
            'ncollection_account_analytics.analytic_plan_cost_center')
        cls.north = cls.env['account.analytic.account'].create({
            'name': 'North', 'plan_id': cls.cc_plan.id})
        cls.south = cls.env['account.analytic.account'].create({
            'name': 'South', 'plan_id': cls.cc_plan.id})

        # One 1,000 revenue entry SPLIT 60/40 across the two cost centres.
        move = cls.env['account.move'].create({
            'move_type': 'entry', 'journal_id': cls.journal.id,
            'date': date(2026, 3, 10),
            'line_ids': [
                (0, 0, {'account_id': cls.receivable.id, 'debit': 1000.0,
                        'credit': 0.0}),
                (0, 0, {'account_id': cls.revenue.id, 'debit': 0.0,
                        'credit': 1000.0,
                        'analytic_distribution': {str(cls.north.id): 60.0,
                                                  str(cls.south.id): 40.0}}),
            ]})
        move.action_post()

        cls.date_from = date(2026, 1, 1)
        cls.date_to = date(2026, 12, 31)

    # ---- the dimension ---------------------------------------------------

    def test_the_three_plans_are_seeded_as_independent_roots(self):
        """Odoo validates the 100% rule per ROOT plan, which is the entire
        reason this module defines no dimension model of its own."""
        plans = [self.Dim._nc_plan(d) for d in
                 ('department', 'cost_center', 'profit_center')]
        for plan in plans:
            self.assertTrue(plan, "a seeded analytic plan is missing")
            self.assertFalse(plan.parent_id,
                             "%s is not a ROOT plan, so its distribution would "
                             "be validated together with another dimension's"
                             % plan.name)
        self.assertEqual(len({p.id for p in plans}), 3)

    def test_a_split_item_is_apportioned_not_double_counted(self):
        """1,000 split 60/40 is 600 and 400 — the assertion this file exists
        for. Grouping move lines by distribution would report 1,000 twice."""
        rows = {row['name']: row['amount'] for row in
                self.Dim._nc_breakdown('cost_center', self.date_from, self.date_to)}
        self.assertAlmostEqual(rows['North'], 600.0, places=2)
        self.assertAlmostEqual(rows['South'], 400.0, places=2)
        self.assertAlmostEqual(
            self.Dim._nc_total('cost_center', self.date_from, self.date_to),
            1000.0, places=2,
            msg="the dimension total is not the amount actually posted")

    def test_a_draft_distribution_contributes_nothing(self):
        """The breakdown filters no move state — and must not need to.

        Odoo creates ``account.analytic.line`` only for POSTED move lines: both
        call sites are gated on it (``_inverse_analytic_distribution`` browses
        ``parent_state == "posted"``, and ``account_move`` creates them at
        posting). So this service is implicitly posted-only, matching the KPIs
        beside it, which say so explicitly.

        That is a guarantee borrowed from Odoo rather than enforced here, and an
        unstated borrowed guarantee is one that quietly stops holding. This
        asserts it: a 5,000 distribution left in draft must move nothing.
        """
        before = self.Dim._nc_total('cost_center', self.date_from, self.date_to)
        self.env['account.move'].create({
            'move_type': 'entry', 'journal_id': self.journal.id,
            'date': date(2026, 3, 20),
            'line_ids': [
                (0, 0, {'account_id': self.receivable.id, 'debit': 5000.0,
                        'credit': 0.0}),
                (0, 0, {'account_id': self.revenue.id, 'debit': 0.0,
                        'credit': 5000.0,
                        'analytic_distribution': {str(self.north.id): 100.0}}),
            ]})  # deliberately NOT posted
        after = self.Dim._nc_total('cost_center', self.date_from, self.date_to)
        self.assertAlmostEqual(
            before, after, places=2,
            msg="a DRAFT distribution moved the cost-centre total — Odoo no "
                "longer restricts analytic lines to posted moves, so this "
                "service must filter move state itself")

    def test_an_unknown_dimension_returns_none_not_an_empty_list(self):
        """"This tenant cannot answer that" and "no activity" are different,
        and the aggregation engine's contract is that the first is None."""
        self.assertIsNone(
            self.Dim._nc_breakdown('not_a_dimension', self.date_from, self.date_to))
        self.assertIsNone(
            self.Dim._nc_total('not_a_dimension', self.date_from, self.date_to))

    def test_a_dimension_with_no_accounts_is_empty_not_none(self):
        """Departments is seeded but unused here: an EMPTY breakdown, because
        the tenant genuinely has no departments — not None, which would mean
        the dimension does not exist."""
        self.assertEqual(
            self.Dim._nc_breakdown('department', self.date_from, self.date_to), [])

    # ---- variance --------------------------------------------------------

    def test_variance_reports_the_missing_budget_module_rather_than_zero(self):
        """#121 is not installed, so there is no budget. A 0.00 variance would
        read as "exactly on budget" — the opposite of the truth."""
        result = self.Forecast._nc_budget_variance(self.date_from, self.date_to)
        self.assertFalse(result['available'])
        self.assertIn('ncollection_account_budget', result['reason'])
        self.assertNotIn('variance', result,
                         "a variance figure was returned with no budget to "
                         "compare against")

    def test_variance_percentage_uses_an_absolute_denominator(self):
        """A cost budgeted at -100 and spent at -150 is a 50% OVERSPEND. Over a
        signed denominator it would report +50%, reading as favourable."""
        _variance, pct = self.Forecast._nc_variance(-150.0, -100.0)
        self.assertAlmostEqual(pct, -50.0, places=2)
        variance, pct = self.Forecast._nc_variance(120.0, 100.0)
        self.assertAlmostEqual(variance, 20.0, places=2)
        self.assertAlmostEqual(pct, 20.0, places=2)

    def test_variance_percentage_is_none_against_a_zero_budget(self):
        self.assertIsNone(self.Forecast._nc_variance(50.0, 0.0)[1])

    # ---- the trend -------------------------------------------------------

    def test_the_trend_recovers_a_known_straight_line(self):
        """A perfectly linear series must give back its own slope, or the fit
        is not doing arithmetic."""
        trend = self.Forecast._nc_trend([100.0, 200.0, 300.0, 400.0])
        self.assertAlmostEqual(trend['slope'], 100.0, places=6)
        self.assertAlmostEqual(trend['intercept'], 100.0, places=6)
        self.assertEqual(trend['periods'], 4)

    def test_extrapolation_continues_that_line(self):
        """100/200/300/400 -> the next period is 500."""
        forecast = self.Forecast._nc_extrapolate([100.0, 200.0, 300.0, 400.0])
        self.assertAlmostEqual(forecast['value'], 500.0, places=6)
        self.assertAlmostEqual(
            self.Forecast._nc_extrapolate(
                [100.0, 200.0, 300.0, 400.0], ahead=3)['value'], 700.0, places=6)

    def test_the_method_travels_with_the_number(self):
        """A forecast whose derivation is not attached to it is a number a
        reader will assume more of than it deserves."""
        forecast = self.Forecast._nc_extrapolate([1.0, 2.0, 3.0])
        self.assertIn('least squares', forecast['method'])
        self.assertIn('no seasonality', forecast['method'])
        self.assertEqual(forecast['periods'], 3)

    def test_too_little_history_refuses_to_forecast(self):
        """Two points always fit a line perfectly. Calling that a forecast is
        the failure mode this guard exists for."""
        self.assertIsNone(self.Forecast._nc_trend([100.0, 200.0]))
        self.assertIsNone(self.Forecast._nc_extrapolate([100.0, 200.0]))
        self.assertIsNone(self.Forecast._nc_trend([]))
        self.assertIsNone(self.Forecast._nc_trend(None))

    def test_a_flat_series_trends_flat_rather_than_failing(self):
        """Zero slope is a real answer — "no growth" — unlike too few points."""
        trend = self.Forecast._nc_trend([250.0, 250.0, 250.0])
        self.assertAlmostEqual(trend['slope'], 0.0, places=6)
        self.assertAlmostEqual(
            self.Forecast._nc_extrapolate([250.0, 250.0, 250.0])['value'],
            250.0, places=6)
