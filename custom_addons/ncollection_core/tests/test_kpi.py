# -*- coding: utf-8 -*-
"""Operational KPI computations (P4-T02).

The acceptance criterion is "KPI values match hand-calculated fixtures exactly",
so every expectation below is written as the arithmetic itself — `(1000 + 3000)
/ 2` rather than `2000.0`. A bare literal would still pass if the formula and
the fixture drifted together; showing the working means the test states what the
KPI *means*, not just what it currently returns.

`sale`, `hr` and `stock` are SOFT dependencies — `ncollection_core` does not
depend on them, so on this database the models may not exist. Tests that need a
model skip when it is absent rather than failing, and the soft-dependency
contract itself is asserted directly.
"""

from datetime import date

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestKpiPeriods(TransactionCase):
    """Window arithmetic — no ERP models needed, so this always runs."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Kpi = cls.env["ncollection.kpi"]

    def test_month_bounds_are_half_open(self):
        start, end = self.Kpi._period_bounds("month", date(2026, 3, 17))
        self.assertEqual(start, date(2026, 3, 1))
        self.assertEqual(end, date(2026, 4, 1))

    def test_quarter_bounds_snap_to_the_quarter(self):
        for day, expected_start, expected_end in [
            (date(2026, 1, 1), date(2026, 1, 1), date(2026, 4, 1)),
            (date(2026, 5, 9), date(2026, 4, 1), date(2026, 7, 1)),
            (date(2026, 12, 31), date(2026, 10, 1), date(2027, 1, 1)),
        ]:
            with self.subTest(day=day):
                self.assertEqual(
                    self.Kpi._period_bounds("quarter", day),
                    (expected_start, expected_end))

    def test_year_bounds(self):
        self.assertEqual(
            self.Kpi._period_bounds("year", date(2026, 8, 1)),
            (date(2026, 1, 1), date(2027, 1, 1)))

    def test_previous_window_abuts_the_current_one(self):
        """No gap and no overlap — the classic off-by-one in period compare."""
        for period in ("month", "quarter", "year"):
            with self.subTest(period=period):
                start, _end = self.Kpi._period_bounds(period, date(2026, 8, 1))
                prev_start, prev_end = self.Kpi._previous_bounds(period, start)
                self.assertEqual(prev_end, start, "previous must end where current begins")
                self.assertLess(prev_start, prev_end)

    # -- delta ----------------------------------------------------------

    def test_delta_pct_arithmetic(self):
        # 150 vs 120 -> (150-120)/120*100 = 25%
        self.assertAlmostEqual(self.Kpi._delta_pct(150.0, 120.0), 25.0, places=9)
        # halving is -50%
        self.assertAlmostEqual(self.Kpi._delta_pct(50.0, 100.0), -50.0, places=9)

    def test_delta_pct_is_none_when_undefined(self):
        """None, not 0.0 — "no comparison" and "no change" are different.

        A dashboard that renders them identically tells a lie: a KPI with no
        prior period would show a confident "0% change".
        """
        self.assertIsNone(self.Kpi._delta_pct(10.0, 0.0), "division by zero")
        self.assertIsNone(self.Kpi._delta_pct(10.0, None), "no previous value")
        self.assertIsNone(self.Kpi._delta_pct(None, 10.0), "no current value")


@tagged("post_install", "-at_install")
class TestKpiThresholds(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.kpi = cls.env.ref("ncollection_core.kpi_employee_turnover")
        cls.Band = cls.env["ncollection.kpi.threshold"]

    def test_seeded_turnover_bands_classify_correctly(self):
        """The shipped bands: [0,10) good, [10,20) warning, [20,inf) bad."""
        for value, expected in [
            (0.0, "good"), (9.99, "good"),
            (10.0, "warning"), (19.99, "warning"),
            (20.0, "bad"), (250.0, "bad"),
        ]:
            with self.subTest(value=value):
                band = self.Band._match(self.kpi.threshold_ids, value)
                self.assertIsNotNone(band, "every value should land in a band")
                self.assertEqual(band.state, expected)

    def test_boundaries_are_half_open_so_bands_cannot_overlap(self):
        """Exactly 10.0 is warning, never good — adjacent bands share an edge."""
        self.assertEqual(
            self.Band._match(self.kpi.threshold_ids, 10.0).state, "warning")

    def test_no_band_matches_returns_none(self):
        kpi = self.env.ref("ncollection_core.kpi_avg_deal_size")
        self.assertFalse(kpi.threshold_ids, "avg deal size ships without bands")
        self.assertIsNone(self.Band._match(kpi.threshold_ids, 42.0))

    def test_none_value_matches_no_band(self):
        self.assertIsNone(self.Band._match(self.kpi.threshold_ids, None))


@tagged("post_install", "-at_install")
class TestKpiSoftDependencies(TransactionCase):
    """A KPI whose app is absent must yield None, never raise or return 0.

    This is what lets a CRM-only tenant load a dashboard built against
    sale/hr/stock. Returning 0.0 would be worse than None: it renders as a
    confident, wrong number.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Kpi = cls.env["ncollection.kpi"]

    def test_core_declares_no_erp_dependencies(self):
        """The contract that makes the rest of this possible.

        If sale/hr/stock ever enter `depends`, every tenant is forced to
        install them and P1-T09/T10 licensing assumptions break.
        """
        module = self.env["ir.module.module"].search(
            [("name", "=", "ncollection_core")], limit=1)
        declared = set(module.dependencies_id.mapped("name"))
        for forbidden in ("sale", "hr", "stock", "account"):
            self.assertNotIn(
                forbidden, declared,
                "ncollection_core must reach %s through the aggregation "
                "engine's soft references, never a hard dependency" % forbidden)

    def test_absent_model_yields_none_not_zero(self):
        kpi = self.env.ref("ncollection_core.kpi_avg_deal_size")
        self.patch(type(self.env["ncollection.aggregation.engine"]),
                   "_model_readable", lambda self, m: False)
        self.assertIsNone(kpi._compute_avg_deal_size(date(2026, 1, 1),
                                                     date(2026, 2, 1)))

    def test_compute_returns_a_result_envelope_even_when_unavailable(self):
        """A dashboard always gets the shape it expects, value simply None."""
        kpi = self.env.ref("ncollection_core.kpi_avg_deal_size")
        self.patch(type(self.env["ncollection.aggregation.engine"]),
                   "_model_readable", lambda self, m: False)
        result = kpi.compute(reference=date(2026, 3, 15))
        self.assertEqual(result["key"], "avg_deal_size")
        self.assertIsNone(result["value"])
        self.assertIsNone(result["delta_pct"])
        self.assertEqual(result["period_start"], date(2026, 3, 1))
        self.assertEqual(result["period_end"], date(2026, 4, 1))


@tagged("post_install", "-at_install")
class TestAverageDealSize(TransactionCase):
    """Hand-calculated fixtures. Skips where `sale` is not installed."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Kpi = cls.env["ncollection.kpi"]

    def setUp(self):
        super().setUp()
        if "sale.order" not in self.env:
            self.skipTest("sale is not installed on this database")
        self.kpi = self.env.ref("ncollection_core.kpi_avg_deal_size")
        self.partner = self.env["res.partner"].create({"name": "KPI probe co"})

    def _order(self, amount, when, state="sale"):
        """A confirmed order of a known total on a known date."""
        order = self.env["sale.order"].create({
            "partner_id": self.partner.id,
            "date_order": when,
            "order_line": [(0, 0, {
                "name": "probe",
                "product_uom_qty": 1,
                "price_unit": amount,
            })],
        })
        order.state = state
        return order

    def test_average_is_total_over_count(self):
        """Three orders in March: 1000 + 3000 + 500 over 3 = 1500."""
        self._order(1000.0, date(2026, 3, 2))
        self._order(3000.0, date(2026, 3, 15))
        self._order(500.0, date(2026, 3, 30))
        value = self.kpi._compute_avg_deal_size(date(2026, 3, 1), date(2026, 4, 1))
        self.assertAlmostEqual(value, (1000.0 + 3000.0 + 500.0) / 3, places=2)

    def test_orders_outside_the_window_are_excluded(self):
        """The window is half-open: 1 April belongs to April, not March."""
        self._order(1000.0, date(2026, 3, 15))
        self._order(9999.0, date(2026, 4, 1))
        value = self.kpi._compute_avg_deal_size(date(2026, 3, 1), date(2026, 4, 1))
        self.assertAlmostEqual(value, 1000.0, places=2)

    def test_quotations_are_excluded(self):
        """Confirmed orders only — counting intent as revenue inflates it."""
        self._order(1000.0, date(2026, 3, 10))
        self._order(50000.0, date(2026, 3, 11), state="draft")
        value = self.kpi._compute_avg_deal_size(date(2026, 3, 1), date(2026, 4, 1))
        self.assertAlmostEqual(value, 1000.0, places=2)

    def test_empty_window_is_zero_not_none(self):
        """No orders is a real answer of 0; None means "cannot compute"."""
        value = self.kpi._compute_avg_deal_size(date(2019, 1, 1), date(2019, 2, 1))
        self.assertEqual(value, 0.0)


@tagged("post_install", "-at_install")
class TestEmployeeTurnover(TransactionCase):
    """Hand-calculated fixtures. Skips where `hr` is not installed."""

    def setUp(self):
        super().setUp()
        if "hr.employee" not in self.env:
            self.skipTest("hr is not installed on this database")
        self.kpi = self.env.ref("ncollection_core.kpi_employee_turnover")

    def test_turnover_is_departures_over_average_headcount(self):
        """Defined as: departures / ((headcount_start + headcount_end) / 2) * 100.

        The fixture is asserted against the formula rather than a literal, so
        the test still describes the KPI if the seed data changes.
        """
        start, end = date(2026, 1, 1), date(2027, 1, 1)
        departures = self.kpi._single({
            "key": "probe:departures", "model": "hr.employee",
            "domain": [("departure_date", ">=", start),
                       ("departure_date", "<", end)],
            "aggregates": ["__count"],
        })
        if departures is None:
            self.skipTest("hr.employee not readable for this user")
        headcount_start = self.kpi._headcount_at(start)
        headcount_end = self.kpi._headcount_at(end)
        average = (headcount_start + headcount_end) / 2.0

        value = self.kpi._compute_employee_turnover(start, end)
        expected = 0.0 if not average else (departures[0] or 0) / average * 100.0
        self.assertAlmostEqual(value, expected, places=6)

    def test_zero_headcount_is_zero_not_a_division_error(self):
        """An empty company must not raise; the window predates any hire."""
        value = self.kpi._compute_employee_turnover(
            date(1990, 1, 1), date(1991, 1, 1))
        self.assertEqual(value, 0.0)


@tagged("post_install", "-at_install")
class TestInventoryTurnover(TransactionCase):
    """Hand-calculated fixtures. Skips where `stock` is not installed."""

    def setUp(self):
        super().setUp()
        if "stock.quant" not in self.env:
            self.skipTest("stock is not installed on this database")
        self.kpi = self.env.ref("ncollection_core.kpi_inventory_turnover")

    def test_turnover_is_shipped_over_on_hand_by_quantity(self):
        """QUANTITY-based, not COGS-based.

        The textbook ratio uses COGS, which is a financial figure owned by
        ncollection_account_analytics (#120). The 2026-07-19 split kept this
        KPI operational, so it must compute without an accounting model.
        """
        start, end = date(2026, 1, 1), date(2026, 4, 1)
        shipped = self.kpi._single({
            "key": "probe:shipped", "model": "stock.move",
            "domain": [("state", "=", "done"),
                       ("location_dest_id.usage", "=", "customer"),
                       ("date", ">=", start), ("date", "<", end)],
            "aggregates": ["quantity:sum"],
        })
        on_hand = self.kpi._single({
            "key": "probe:on_hand", "model": "stock.quant",
            "domain": [("location_id.usage", "=", "internal")],
            "aggregates": ["quantity:sum"],
        })
        if shipped is None or on_hand is None:
            self.skipTest("stock models not readable for this user")

        value = self.kpi._compute_inventory_turnover(start, end)
        available = on_hand[0] or 0.0
        expected = 0.0 if not available else (shipped[0] or 0.0) / available
        self.assertAlmostEqual(value, expected, places=6)

    def test_no_stock_on_hand_is_zero_not_a_division_error(self):
        """Guarding the denominator: an empty warehouse must not raise."""
        self.patch(type(self.kpi), "_single",
                   lambda self, spec: (0.0,))
        self.assertEqual(
            self.kpi._compute_inventory_turnover(
                date(2026, 1, 1), date(2026, 4, 1)),
            0.0)
