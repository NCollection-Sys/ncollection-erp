# -*- coding: utf-8 -*-
"""Statistical baseline for anomaly detection (P5-T04) — the pure layer.

These functions decide what counts as an anomaly, so every property worth
protecting here is one whose failure is SILENT:

0. (A `moving_average` helper was removed before merge — unused by every
   detector, so its tests proved nothing about shipped behaviour.)
1. **Too little history is not an anomaly.** A tenant provisioned last week has
   four data points. Reporting "anomalous" on noise is how an alert feature
   trains its users to ignore it.
2. **A flat series must not divide by zero.** Stock held at exactly 100 for
   thirty days has stddev 0. Naive z-scoring raises ZeroDivisionError, or worse
   returns NaN and compares False against every threshold — so the one series
   that most obviously deviates when it finally moves would be the one series
   that can never alert.
3. **Flat AND unchanged is the opposite of an anomaly.** The same stddev-0 case
   with no movement must score 0.0, not "infinitely surprising".
4. **Direction is preserved.** "Sales dropped" and "expenses spiked" are
   different alerts; a detector that only sees magnitude cannot tell them apart.

The layer is deliberately pure Python with no Odoo import, so it can be reasoned
about and tested without a database — and so the ORM cannot smuggle a recordset
into a number.
"""

from odoo.addons.ncollection_core.models.anomaly import statistics as stats
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestAnomalyStatistics(TransactionCase):
    """Pure-function tests; no env used, but kept in the suite that ships."""

    # ---- zscore_of_latest ----------------------------------------------

    def test_obvious_spike_scores_high(self):
        z = stats.zscore_of_latest([10, 10, 11, 9, 10, 40])
        self.assertIsNotNone(z)
        self.assertGreater(z, 3.0)

    def test_obvious_drop_scores_negative(self):
        """Sign carries the direction — a drop and a spike are different alerts."""
        z = stats.zscore_of_latest([100, 98, 102, 99, 101, 20])
        self.assertIsNotNone(z)
        self.assertLess(z, -3.0)

    def test_ordinary_movement_scores_low(self):
        z = stats.zscore_of_latest([10, 11, 9, 10, 11, 10])
        self.assertIsNotNone(z)
        self.assertLess(abs(z), 3.0)

    def test_too_little_history_returns_none(self):
        """None means "no opinion", which must never be read as an anomaly."""
        self.assertIsNone(stats.zscore_of_latest([10, 12]))
        self.assertIsNone(stats.zscore_of_latest([]))

    def test_flat_history_that_stays_flat_is_not_an_anomaly(self):
        self.assertEqual(stats.zscore_of_latest([100, 100, 100, 100, 100]), 0.0)

    def test_flat_history_that_suddenly_moves_is_maximally_anomalous(self):
        """stddev == 0 is the divide-by-zero trap. It is also the clearest
        possible signal: a value that never varied just did."""
        z = stats.zscore_of_latest([100, 100, 100, 100, 250])
        self.assertEqual(z, float('inf'))
        z_down = stats.zscore_of_latest([100, 100, 100, 100, 5])
        self.assertEqual(z_down, float('-inf'))

    def test_baseline_excludes_the_point_under_test(self):
        """The latest value must not dilute the baseline it is measured against.
        Including it is a classic masking bug: a big enough outlier inflates the
        stddev until it stops looking like an outlier."""
        series = [10, 10, 10, 10, 1000]
        z = stats.zscore_of_latest(series)
        self.assertEqual(z, float('inf'))

    # ---- is_anomalous --------------------------------------------------

    def test_is_anomalous_respects_threshold_and_none(self):
        self.assertTrue(stats.is_anomalous(4.2, threshold=3.0))
        self.assertTrue(stats.is_anomalous(-4.2, threshold=3.0))
        self.assertFalse(stats.is_anomalous(1.0, threshold=3.0))
        self.assertFalse(stats.is_anomalous(None, threshold=3.0),
                         "None means no opinion and must never alert")

    def test_infinity_is_anomalous(self):
        self.assertTrue(stats.is_anomalous(float('inf'), threshold=3.0))
        self.assertTrue(stats.is_anomalous(float('-inf'), threshold=3.0))

    # ---- severity_for --------------------------------------------------

    def test_severity_escalates_with_deviation(self):
        self.assertEqual(stats.severity_for(3.2), 'info')
        self.assertEqual(stats.severity_for(-3.2), 'info')
        self.assertEqual(stats.severity_for(5.5), 'warning')
        self.assertEqual(stats.severity_for(12.0), 'critical')
        self.assertEqual(stats.severity_for(float('inf')), 'critical')

    def test_severity_below_threshold_is_none(self):
        self.assertIsNone(stats.severity_for(1.0))
        self.assertIsNone(stats.severity_for(None))
