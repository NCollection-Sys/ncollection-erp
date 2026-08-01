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
    """Real records, literal expected values.

    An earlier version of this class was TAUTOLOGICAL: it built `expected` by
    calling `_headcount_at` — the very helper under test — so a wrong domain
    would have agreed with itself and passed. It proved only that a division
    works. These tests build a roster with known dates and assert the number a
    person would compute on paper.
    """

    def setUp(self):
        super().setUp()
        if "hr.employee" not in self.env:
            self.skipTest("hr is not installed on this database")
        self.kpi = self.env.ref("ncollection_core.kpi_employee_turnover")
        # Installing `hr` creates an employee for the admin user, and its
        # create_date lands inside these windows — which silently shifted the
        # denominator and made the first run of these tests report 1/4 where
        # 1/3.5 was expected.
        #
        # Rather than DELETE those rows (an earlier attempt did, and wrecked the
        # test database badly enough to uninstall every module), push their hire
        # date past every window used here. `create_date < moment` then excludes
        # them from any headcount, and with no departure_date they contribute no
        # departures either. Non-destructive, and rolled back with the test.
        existing = self.env["hr.employee"].with_context(
            active_test=False).search([])
        if existing:
            self.env.cr.execute(
                "UPDATE hr_employee SET create_date = %s WHERE id IN %s",
                ("2099-01-01", tuple(existing.ids)))
            existing.invalidate_recordset(["create_date"])

    def _employee(self, name, hired, departed=None):  # noqa: D401
        """An employee hired on a date, optionally departed on another.

        `create_date` is an auto-set magic column, so the hire signal is
        backdated with SQL — the same technique the auth-log retention tests
        use, and the only way to set it.
        """
        employee = self.env["hr.employee"].create({"name": name})
        if departed:
            employee.departure_date = departed
            employee.active = False
        self.env.cr.execute(
            "UPDATE hr_employee SET create_date = %s WHERE id = %s",
            (hired, employee.id))
        employee.invalidate_recordset(["create_date"])
        return employee

    def _turnover(self):
        return self.kpi._compute_employee_turnover(
            date(2026, 1, 1), date(2027, 1, 1))

    def test_turnover_matches_a_hand_calculated_roster(self):
        """Four hired in 2025, one leaves in 2026.

            headcount(2026-01-01) = 4   (all hired, none gone)
            headcount(2027-01-01) = 3   (one departed 2026-03-15)
            average headcount     = (4 + 3) / 2 = 3.5
            departures in 2026    = 1
            turnover              = 1 / 3.5 * 100 = 28.571...%
        """
        for name in ("Ada", "Grace", "Katherine"):
            self._employee(name, hired="2025-06-01")
        self._employee("Departed One", hired="2025-06-01", departed="2026-03-15")

        self.assertAlmostEqual(self._turnover(), 1 / 3.5 * 100, places=6)

    def test_a_hire_after_the_window_does_not_inflate_headcount(self):
        """Someone hired in 2027 was not staff during 2026.

            headcount(2026-01-01) = 2, headcount(2027-01-01) = 1
            average = 1.5, departures = 1  ->  1 / 1.5 * 100 = 66.67%
        The 2027 hire must not appear in either headcount.
        """
        self._employee("Stayer", hired="2025-01-01")
        self._employee("Leaver", hired="2025-01-01", departed="2026-07-01")
        self._employee("Future Hire", hired="2027-06-01")

        self.assertAlmostEqual(self._turnover(), 1 / 1.5 * 100, places=6)

    def test_a_departure_outside_the_window_is_not_counted(self):
        """Leaving in 2025 is not a 2026 departure.

            headcount(2026-01-01) = 1 (the leaver was already gone)
            headcount(2027-01-01) = 1
            departures in 2026 = 0  ->  0%
        """
        self._employee("Stayer", hired="2024-01-01")
        self._employee("Old Leaver", hired="2024-01-01", departed="2025-05-05")

        self.assertAlmostEqual(self._turnover(), 0.0, places=6)

    def test_departed_employees_still_count_toward_headcount(self):
        """Odoo archives an employee on departure.

        Without `active_test=False` the archived leaver would vanish from
        headcount(2026-01-01), making the denominator 1 instead of 2 and
        DOUBLING the reported turnover. This test is the guard on that.

            headcount(2026-01-01) = 2 (leaver still employed on 1 Jan)
            headcount(2027-01-01) = 1
            average = 1.5, departures = 1  ->  66.67%, not 100%
        """
        self._employee("Stayer", hired="2025-01-01")
        self._employee("Leaver", hired="2025-01-01", departed="2026-06-30")

        value = self._turnover()
        self.assertAlmostEqual(value, 1 / 1.5 * 100, places=6)
        self.assertNotAlmostEqual(
            value, 100.0, places=6,
            msg="archived leaver was excluded from headcount")

    def test_bulk_imported_roster_shares_a_create_date(self):
        """The documented weak spot, pinned so it cannot regress silently.

        `create_date` stands in for a hire date. A tenant migrating its roster
        stamps every employee with import day, so any window that STARTS before
        the import sees a headcount of 0. The KPI then reports 0.0 rather than a
        confidently wrong percentage — that is the behaviour being locked in.
        """
        self._employee("Imported A", hired="2026-09-01")
        self._employee("Imported B", hired="2026-09-01", departed="2026-10-01")

        value = self.kpi._compute_employee_turnover(
            date(2026, 1, 1), date(2026, 6, 1))
        self.assertEqual(
            value, 0.0,
            "a window entirely before the import must yield 0, not a guess")

    def test_zero_headcount_is_zero_not_a_division_error(self):
        self.assertEqual(
            self.kpi._compute_employee_turnover(
                date(1990, 1, 1), date(1991, 1, 1)),
            0.0)


@tagged("post_install", "-at_install")
class TestInventoryTurnover(TransactionCase):
    """Real stock records, literal expected values.

    Also previously tautological — it re-ran the implementation's own domains
    to build `expected`. Now it creates known quantities and asserts the ratio
    a person would compute.
    """

    def setUp(self):
        super().setUp()
        if "stock.quant" not in self.env:
            self.skipTest("stock is not installed on this database")
        self.kpi = self.env.ref("ncollection_core.kpi_inventory_turnover")
        self.product = self.env["product.product"].create({
            "name": "KPI turnover widget",
            "is_storable": True,
        })
        self.stock_loc = self.env.ref("stock.stock_location_stock")
        self.customer_loc = self.env.ref("stock.stock_location_customers")
        # Any pre-existing internal stock would land in the denominator, since
        # the KPI sums on-hand across ALL internal locations. Zero it rather
        # than unlink it — quants are referenced elsewhere and deleting them is
        # a heavier hammer than a fixture needs.
        self.env["stock.quant"].search(
            [("location_id.usage", "=", "internal")]).quantity = 0.0

    def _on_hand(self, quantity):
        """SET on-hand to an exact figure. Call AFTER creating moves.

        A `state='done'` move decrements the source quant, so seeding on-hand
        first and shipping afterwards left the denominator at 100 - 999 and the
        KPI returned a NEGATIVE ratio on the first run. Forcing the quant last
        makes the denominator exactly what the test claims it is.
        """
        # Zero every internal quant first. The KPI sums on-hand across ALL
        # internal locations, so a move that lands stock in another internal
        # bay (an internal transfer) would otherwise inflate the denominator —
        # which is exactly how test_internal_transfers_are_not_shipments first
        # read 10/600 instead of 10/100.
        self.env["stock.quant"].search(
            [("location_id.usage", "=", "internal")]).quantity = 0.0
        quant = self.env["stock.quant"].search([
            ("product_id", "=", self.product.id),
            ("location_id", "=", self.stock_loc.id),
        ], limit=1)
        if quant:
            quant.quantity = quantity
        else:
            self.env["stock.quant"].create({
                "product_id": self.product.id,
                "location_id": self.stock_loc.id,
                "quantity": quantity,
            })

    def _shipped(self, quantity, when):
        """A completed outbound move of a known quantity on a known date."""
        move = self.env["stock.move"].create({
            "product_id": self.product.id,
            "product_uom_qty": quantity,
            "quantity": quantity,
            "location_id": self.stock_loc.id,
            "location_dest_id": self.customer_loc.id,
            "state": "done",
        })
        self.env.cr.execute(
            "UPDATE stock_move SET date = %s WHERE id = %s", (when, move.id))
        move.invalidate_recordset(["date"])
        return move

    def _turnover(self):
        return self.kpi._compute_inventory_turnover(
            date(2026, 1, 1), date(2026, 4, 1))

    def test_turnover_matches_hand_calculated_quantities(self):
        """Shipped 30 + 20 = 50 units against 200 on hand -> 0.25 turns.

        Quantity-based, never COGS — the financial variant belongs to #120.
        """
        self._shipped(30.0, "2026-01-15 10:00:00")
        self._shipped(20.0, "2026-03-20 10:00:00")
        self._on_hand(200.0)

        self.assertAlmostEqual(self._turnover(), (30.0 + 20.0) / 200.0, places=6)

    def test_moves_outside_the_window_are_excluded(self):
        """1 April belongs to the next quarter — the window is half-open."""
        self._shipped(10.0, "2026-02-01 10:00:00")
        self._shipped(999.0, "2026-04-01 00:00:00")
        self._on_hand(100.0)

        self.assertAlmostEqual(self._turnover(), 10.0 / 100.0, places=6)

    def test_internal_transfers_are_not_shipments(self):
        """Only moves whose destination is a CUSTOMER count as shipped.

        Moving stock between two internal locations is not turnover; counting
        it would let warehouse reorganisation inflate the number.
        """
        self._shipped(10.0, "2026-02-01 10:00:00")
        internal = self.env["stock.location"].create({
            "name": "KPI probe bay", "usage": "internal",
            "location_id": self.stock_loc.id,
        })
        self.env["stock.move"].create({
            "product_id": self.product.id,
            "product_uom_qty": 500.0, "quantity": 500.0,
            "location_id": self.stock_loc.id,
            "location_dest_id": internal.id,
            "state": "done",
        })
        self._on_hand(100.0)

        self.assertAlmostEqual(self._turnover(), 10.0 / 100.0, places=6)

    def test_no_stock_on_hand_is_zero_not_a_division_error(self):
        self.patch(type(self.kpi), "_single", lambda self, spec: (0.0,))
        self.assertEqual(self._turnover(), 0.0)
