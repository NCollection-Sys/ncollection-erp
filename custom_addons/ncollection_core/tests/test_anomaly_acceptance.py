# -*- coding: utf-8 -*-
"""P5-T04 acceptance: seeded anomalies are detected with ZERO false negatives.

This file IS the acceptance criterion. The ticket says:

    "seeded anomalies in demo data are detected with zero false negatives on
     the test set."

so the test set has to be an explicit, labelled thing rather than a vibe. Each
case below states what it is and whether a detector should fire on it, and the
suite fails if any case labelled `should_alert=True` produces no alert.

**Why synthetic rather than the demo tenant.** The labels are the point. Real
demo data has no ground truth attached — nobody can say which of Al Barari's
Tuesdays is "actually" anomalous, so "zero false negatives" would be
unfalsifiable against it. A fixture where the anomalies are placed by hand is
the only version of this bar that can be checked, and it runs in CI, on any
machine, without `make demo-tenant` state.

**False positives are reported, not enforced.** The ticket bars false negatives
only — and a detector that alerted on everything would satisfy that trivially.
So the suite also counts false positives and asserts they stay at zero for these
cases, which is what stops "detect everything" from passing.

The aggregation engine is stubbed, deliberately: these cases test the DETECTION
logic against known series. Whether `sale.order` is installed on a given tenant
is the engine's business (and is covered by test_aggregation_engine.py), not
this file's.
"""

from odoo.tests import TransactionCase, tagged

# ---------------------------------------------------------------------------
# The labelled test set
# ---------------------------------------------------------------------------
# (label, detector, series, should_alert)
#
# Series are daily values, oldest first; the last point is "today". 30-ish flat
# points with noise is a normal business rhythm; the seeded anomaly is the final
# value where one is intended.
_NORMAL = [100, 104, 98, 102, 99, 101, 103, 97, 100, 102]

TEST_SET = (
    # --- sales: only a DROP is an incident -------------------------------
    ("sales: steady trading",            'sales_trend_drop', _NORMAL + [101], False),
    ("sales: collapse to near zero",     'sales_trend_drop', _NORMAL + [3],   True),
    ("sales: severe drop",               'sales_trend_drop', _NORMAL + [30],  True),
    ("sales: record day is NOT an alert", 'sales_trend_drop', _NORMAL + [400], False),
    ("sales: flat then collapse",        'sales_trend_drop', [50] * 10 + [1], True),

    # --- expenses: only a SPIKE is an incident ---------------------------
    ("expenses: steady spend",           'expense_spike', _NORMAL + [100], False),
    ("expenses: duplicate-bill spike",   'expense_spike', _NORMAL + [900], True),
    ("expenses: quiet day is NOT alert", 'expense_spike', _NORMAL + [2],   False),
    ("expenses: flat then spike",        'expense_spike', [20] * 10 + [500], True),

    # --- attendance: BOTH directions matter ------------------------------
    ("attendance: normal week",          'attendance_anomaly', [40, 41, 39, 40, 42, 38, 40], False),
    ("attendance: mass absence",         'attendance_anomaly', [40, 41, 39, 40, 42, 38, 4], True),
    ("attendance: unexplained surge",    'attendance_anomaly', [40, 41, 39, 40, 42, 38, 200], True),

    # --- not enough history is never an anomaly --------------------------
    ("new tenant: two data points",      'sales_trend_drop', [100, 3], False),
    ("new tenant: empty history",        'sales_trend_drop', [], False),
)

_DATE_FIELD = {
    'sales_trend_drop': 'date_order:day',
    'expense_spike': 'invoice_date:day',
    'attendance_anomaly': 'check_in:day',
}


@tagged("post_install", "-at_install")
class TestAnomalyAcceptance(TransactionCase):

    def setUp(self):
        super().setUp()
        self.Detector = self.env['ncollection.anomaly.detector']
        self.Alert = self.env['ncollection.alert']

    def _stub_engine(self, series):
        """Make the aggregation engine return one labelled series.

        Rows are positional tuples — (groupby…, aggregate…) — which is the
        engine's documented shape (`_read_group_uncached` flattens to tuples).
        Matching it here is what keeps this stub honest.
        """
        rows = [("2026-08-%02d" % (i + 1), value) for i, value in enumerate(series)]
        Engine = type(self.env['ncollection.aggregation.engine'])
        self.patch(Engine, 'aggregate',
                   lambda engine_self, spec: {'key': spec['key'], 'rows': rows,
                                              'cached': False})

    def test_zero_false_negatives_on_the_seeded_test_set(self):
        """THE acceptance criterion. Every seeded anomaly must be detected."""
        false_negatives = []
        false_positives = []

        for label, detector_key, series, should_alert in TEST_SET:
            self._stub_engine(series)
            findings = self.Detector.detect(detector_key)
            fired = bool(findings)

            if should_alert and not fired:
                false_negatives.append(label)
            elif not should_alert and fired:
                false_positives.append(label)

        self.assertEqual(
            false_negatives, [],
            "FALSE NEGATIVES — seeded anomalies that went undetected: %s. "
            "This is the acceptance criterion for P5-T04 and it is not "
            "negotiable: an anomaly detector that misses seeded anomalies is "
            "worse than none, because it is trusted." % false_negatives)

        self.assertEqual(
            false_positives, [],
            "FALSE POSITIVES — normal series that raised an alert: %s. Not "
            "barred by the ticket, but enforced here so 'alert on everything' "
            "cannot satisfy the zero-false-negative bar." % false_positives)

    def test_every_detected_anomaly_becomes_one_alert_record(self):
        """Detection is worthless if it does not survive into a record."""
        self._stub_engine(_NORMAL + [3])
        findings = self.Detector.detect('sales_trend_drop')
        self.assertTrue(findings, "precondition: the drop must be detected")

        alerts = self.env['ncollection.alert'].browse()
        for finding in findings:
            alerts |= self.Alert._record_finding(finding)

        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts.detector_key, 'sales_trend_drop')
        self.assertIn(alerts.severity, ('info', 'warning', 'critical'))
        self.assertTrue(alerts.suggested_action,
                        "an alert with no suggested action is a notification, "
                        "and the ticket asks for a suggested action")

    def test_rerunning_the_detector_does_not_duplicate_alerts(self):
        """Cron hygiene: the invariant requires every ir.cron to be idempotent.

        Without this the same drop would raise a fresh alert every single day
        it stayed inside the 30-day window.
        """
        self._stub_engine(_NORMAL + [3])
        findings = self.Detector.detect('sales_trend_drop')

        first = self.Alert._record_finding(findings[0])
        second = self.Alert._record_finding(findings[0])

        self.assertTrue(first, "the first run must record the alert")
        self.assertFalse(second, "the second run must be a no-op, not a duplicate")
        self.assertEqual(
            self.Alert.search_count([('dedup_key', '=', findings[0]['dedup_key'])]), 1)

    def test_absent_model_yields_no_findings_and_no_crash(self):
        """A tenant without `sale` (or `hr`) must simply produce nothing.

        The engine returns None for a model this database does not have. That
        is the soft-dependency contract the whole design rests on: verified
        live, `sale` and `hr` are uninstalled on the dev tenant while `account`
        and `stock` are installed.
        """
        Engine = type(self.env['ncollection.aggregation.engine'])
        self.patch(Engine, 'aggregate', lambda engine_self, spec: None)

        for detector_key in ('sales_trend_drop', 'expense_spike',
                             'attendance_anomaly', 'stock_below_safety'):
            self.assertEqual(
                self.Detector.detect(detector_key), [],
                "%s must produce nothing when its model is unavailable"
                % detector_key)

    def test_a_failing_detector_cannot_sink_the_others(self):
        """One detector raising must not cost the cron the remaining three."""
        Engine = type(self.env['ncollection.aggregation.engine'])

        def explode(engine_self, spec):
            raise RuntimeError("simulated ORM failure")

        self.patch(Engine, 'aggregate', explode)
        self.assertEqual(self.Detector.detect('sales_trend_drop'), [])

        # And the cron itself completes rather than propagating.
        #
        # `_commit_progress` really commits — that is the whole point of it in
        # production, since it is what makes a paused batch keep the work it
        # already did. Odoo forbids a commit inside a TransactionCase (it would
        # break the test's own rollback), so the progress call is stubbed to a
        # generous remaining budget. What is under test here is the loop's
        # resilience, not Odoo's commit.
        Cron = type(self.env['ir.cron'])
        self.patch(Cron, '_commit_progress',
                   lambda cron_self, processed=0, remaining=None, deactivate=False: 60.0)
        self.assertTrue(self.Alert._cron_detect_anomalies())

    def test_cron_stops_when_its_time_budget_runs_out(self):
        """The batching must actually stop, or it is decoration.

        A tenant DB has no queue_job runner to escalate long work to, and
        odoo-bus runs every tenant's crons on two threads — so a detector loop
        that ignored the budget would starve other tenants exactly the way #310
        starved other crons.
        """
        Engine = type(self.env['ncollection.aggregation.engine'])
        self.patch(Engine, 'aggregate', lambda engine_self, spec: None)

        calls = []
        Cron = type(self.env['ir.cron'])

        def budget_exhausted(cron_self, processed=0, remaining=None, deactivate=False):
            calls.append(processed)
            return 0.0        # no seconds left after the first detector

        self.patch(Cron, '_commit_progress', budget_exhausted)
        self.Alert._cron_detect_anomalies()

        # One declaration call (processed=0) + exactly one detector step, then
        # the loop must break rather than running the remaining three.
        self.assertEqual(
            calls, [0, 1],
            "the cron must stop after the first step once its budget is gone, "
            "instead it kept going: %s" % calls)
