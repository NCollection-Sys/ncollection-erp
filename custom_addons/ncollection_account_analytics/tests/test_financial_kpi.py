# -*- coding: utf-8 -*-
"""F4-T01: the three financial KPIs, against hand-computed fixtures.

Every expected number below is worked out in the comment beside it from the
fixture, never read back from the code under test. A KPI that renders a
confident wrong number on a CEO dashboard is worse than one that renders
nothing, because nothing prompts a question and a wrong number does not.

The ``None`` cases get as much attention as the value cases. "Cannot be
computed" and "zero" are different statements about a business, and the whole
contract this module shares with ``ncollection.kpi`` is that the first is
reported as ``None`` so a dashboard can omit the tile.
"""
from datetime import date

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.tests import tagged


@tagged('post_install', '-at_install')
class TestFinancialKpi(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.KPI = cls.env['ncollection.account.analytics.kpi']
        cls.receivable = cls.company_data['default_account_receivable']
        cls.revenue = cls.company_data['default_account_revenue']
        cls.journal = cls.company_data['default_journal_misc']
        cls.cogs = cls.env['account.account'].create({
            'name': 'Cost of Goods Sold', 'code': 'NCCOGS',
            'account_type': 'expense_direct_cost',
            'company_ids': [(6, 0, cls.env.company.ids)]})

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

        # staticmethod: a bare function assigned to a class attribute
        # becomes a bound method, so `self.entry(a, b, c, d)` would pass
        # `self` as the first argument.
        cls.entry = staticmethod(entry)
        # --- the fixture ---------------------------------------------------
        # MARCH 2026 (the reference period):
        #   revenue 1,000 on credit; cost of sales 400.
        entry(date(2026, 3, 10), cls.receivable, cls.revenue, 1000.0)
        entry(date(2026, 3, 12), cls.cogs, cls.receivable, 400.0)
        # FEBRUARY 2026 (the period before): revenue 800, no cost of sales.
        entry(date(2026, 2, 10), cls.receivable, cls.revenue, 800.0)

        cls.march = date(2026, 3, 15)

    def _kpi(self, key, **overrides):
        values = {'key': key, 'name': key, 'unit': 'percent',
                  'period_type': 'month'}
        values.update(overrides)
        existing = self.KPI.search([('key', '=', key)], limit=1)
        if existing:
            existing.write(values)
            return existing
        return self.KPI.create(values)

    # ---- Gross Margin % --------------------------------------------------

    def test_gross_margin_is_revenue_less_cost_of_sales_over_revenue(self):
        """(1000 - 400) / 1000 = 60%."""
        data = self._kpi('gross_margin').compute(reference=self.march)
        self.assertAlmostEqual(data['value'], 60.0, places=2)
        # February had revenue 800 and no cost of sales -> 100%.
        self.assertAlmostEqual(data['previous'], 100.0, places=2)

    def test_gross_margin_is_none_without_revenue(self):
        """A margin on nothing is undefined. 0% would read as "sold at cost"."""
        data = self._kpi('gross_margin').compute(reference=date(2026, 1, 15))
        self.assertIsNone(data['value'],
                          "January had no revenue, so the margin is undefined "
                          "— a rendered 0% would be a false statement")

    def test_gross_margin_excludes_operating_expenses(self):
        """Only `expense_direct_cost` is cost of sales. Including operating
        expenses would silently turn this into NET margin under the same name."""
        opex = self.company_data['default_account_expense']
        self.entry(date(2026, 3, 20), opex, self.receivable, 300.0)
        data = self._kpi('gross_margin').compute(reference=self.march)
        self.assertAlmostEqual(data['value'], 60.0, places=2,
                               msg="operating expense leaked into cost of sales")

    # ---- Revenue Growth % ------------------------------------------------

    def test_revenue_growth_compares_with_the_period_before(self):
        """March 1,000 vs February 800 -> (1000-800)/800 = +25%."""
        data = self._kpi('revenue_growth').compute(reference=self.march)
        self.assertAlmostEqual(data['value'], 25.0, places=2)

    def test_revenue_growth_is_none_when_the_prior_period_had_none(self):
        """Growth from nothing is not 0%, not infinite, and not renderable."""
        data = self._kpi('revenue_growth').compute(reference=date(2026, 2, 15))
        # January had no revenue, so February's growth is undefined.
        self.assertIsNone(data['value'])

    # ---- DSO -------------------------------------------------------------

    def test_dso_is_receivables_over_revenue_times_days(self):
        """Receivable balance at 31 March = 1000 - 400 + 800 = 1,400.
        March revenue = 1,000. March has 31 days.
            1400 / 1000 * 31 = 43.4 days
        """
        data = self._kpi('dso', unit='days').compute(reference=self.march)
        self.assertAlmostEqual(data['value'], 43.4, places=2)

    def test_dso_measures_receivables_at_the_last_day_inside_the_period(self):
        """The window is half-open, so a 1 April posting must not count.

        Using the exclusive `end` would pull the next period's first day into
        this period's closing position — an off-by-one that inflates DSO and is
        invisible without a fixture posted exactly on the boundary.
        """
        self.entry(date(2026, 4, 1), self.receivable, self.revenue, 9999.0)
        data = self._kpi('dso', unit='days').compute(reference=self.march)
        self.assertAlmostEqual(data['value'], 43.4, places=2,
                               msg="a 1 April receivable leaked into March's "
                                   "closing position")

    def test_dso_is_none_without_revenue(self):
        data = self._kpi('dso', unit='days').compute(reference=date(2026, 1, 15))
        self.assertIsNone(data['value'])

    # ---- the shared contract with ncollection.kpi ------------------------

    def test_the_result_shape_matches_the_operational_kpi_service(self):
        """A dashboard renders both models with one code path, so the payload
        keys must agree. Divergence here is a broken tile, not a test detail."""
        data = self._kpi('gross_margin').compute(reference=self.march)
        for key in ('key', 'name', 'unit', 'value', 'previous', 'delta_pct',
                    'target', 'period_start', 'period_end'):
            self.assertIn(key, data)

    def test_delta_pct_is_none_rather_than_zero_without_a_comparison(self):
        """"No comparison available" and "no change" are different claims."""
        self.assertIsNone(self.KPI._nc_delta_pct(10.0, 0.0))
        self.assertIsNone(self.KPI._nc_delta_pct(10.0, None))
        self.assertAlmostEqual(self.KPI._nc_delta_pct(150.0, 100.0), 50.0)

    def test_delta_pct_uses_an_absolute_denominator(self):
        """Over a signed one, a figure worsening from -100 to -150 reports as
        +50% — the opposite of what happened."""
        self.assertAlmostEqual(
            self.KPI._nc_delta_pct(-150.0, -100.0), -50.0, places=2)

    def test_draft_entries_do_not_move_a_kpi(self):
        """A draft invoice is an intention. A KPI that counted it would move
        when nothing had happened."""
        before = self._kpi('gross_margin').compute(reference=self.march)['value']
        self.env['account.move'].create({
            'move_type': 'entry', 'journal_id': self.journal.id,
            'date': date(2026, 3, 25),
            'line_ids': [
                (0, 0, {'account_id': self.receivable.id, 'debit': 5000.0,
                        'credit': 0.0}),
                (0, 0, {'account_id': self.revenue.id, 'debit': 0.0,
                        'credit': 5000.0}),
            ]})  # deliberately NOT posted
        after = self._kpi('gross_margin').compute(reference=self.march)['value']
        self.assertAlmostEqual(before, after, places=2)

    def test_the_seeded_definitions_exist_and_are_computable(self):
        """The three KPIs ship as data; a key with no computation method would
        be a definition that silently returns nothing."""
        for xmlid in ('kpi_revenue_growth', 'kpi_gross_margin', 'kpi_dso'):
            kpi = self.env.ref('ncollection_account_analytics.%s' % xmlid)
            with self.subTest(kpi=kpi.key):
                self.assertTrue(
                    hasattr(kpi, '_compute_%s' % kpi.key),
                    "seeded KPI %r has no computation method" % kpi.key)
                kpi.compute(reference=self.march)  # must not raise
