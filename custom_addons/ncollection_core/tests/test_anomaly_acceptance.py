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
from odoo.tools import mute_logger

from ..models.anomaly import detectors as det

_DETECTOR_LOGGER = 'odoo.addons.ncollection_core.models.anomaly.detectors'

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

    # ------------------------------------------------------------------
    # stock_below_safety — the fourth detector
    # ------------------------------------------------------------------
    # This detector had NO acceptance coverage in the first version of this
    # file, and a real multi-warehouse false negative shipped inside that blind
    # spot (found by review against a live database). It is a threshold
    # detector, so it takes the two-spec path rather than the statistical one
    # and needs its own stub.

    def _stub_engine_keyed(self, by_key):
        """Stub the engine per spec `key`, returning None for anything absent.

        The stock detector issues TWO specs — `orderpoint_minimums` and
        `stock_on_hand` — so a single-series stub cannot exercise it. Returning
        None for an unknown key also reproduces the engine's real "model not
        available" answer.
        """
        Engine = type(self.env['ncollection.aggregation.engine'])

        def fake(engine_self, spec):
            rows = by_key.get(spec['key'])
            if rows is None:
                return None
            return {'key': spec['key'], 'rows': rows, 'cached': False}

        self.patch(Engine, 'aggregate', fake)

    def test_stock_below_minimum_in_a_single_warehouse_is_detected(self):
        self._stub_engine_keyed({
            'orderpoint_minimums': [((7, 'Widget'), (1, 'WH1'), 100.0)],
            'stock_on_hand': [((7, 'Widget'), (1, 'WH1'), 20.0)],
        })
        findings = self.Detector.detect('stock_below_safety')
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]['detector_key'], 'stock_below_safety')
        self.assertEqual(findings[0]['severity'], 'warning')   # 80% short

    def test_stock_at_or_above_minimum_is_not_an_alert(self):
        self._stub_engine_keyed({
            'orderpoint_minimums': [((7, 'Widget'), (1, 'WH1'), 100.0)],
            'stock_on_hand': [((7, 'Widget'), (1, 'WH1'), 100.0)],
        })
        self.assertEqual(self.Detector.detect('stock_below_safety'), [])

    def test_completely_out_of_stock_is_critical(self):
        self._stub_engine_keyed({
            'orderpoint_minimums': [((7, 'Widget'), (1, 'WH1'), 100.0)],
            'stock_on_hand': [],           # no quant row at all for it
        })
        findings = self.Detector.detect('stock_below_safety')
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]['severity'], 'critical')
        self.assertEqual(findings[0]['observed_value'], 0.0,
                         "no quant row means no stock, not 'skip this product'")

    def test_a_stockout_in_one_warehouse_is_not_masked_by_another(self):
        """THE regression this detector shipped with.

        Grouping both sides by product alone collapsed every warehouse's
        reordering rule into one number and summed on-hand across all of them,
        so Warehouse 1 being completely empty was invisible whenever Warehouse 2
        happened to be carrying enough. Reproduced live before the fix; pinned
        here so it cannot come back.
        """
        self._stub_engine_keyed({
            'orderpoint_minimums': [
                ((7, 'Widget'), (1, 'WH1'), 100.0),
                ((7, 'Widget'), (2, 'WH2'), 100.0),
            ],
            'stock_on_hand': [
                ((7, 'Widget'), (2, 'WH2'), 110.0),   # WH2 healthy
                # WH1 absent entirely -> zero on hand
            ],
        })
        findings = self.Detector.detect('stock_below_safety')
        self.assertEqual(
            len(findings), 1,
            "the empty warehouse must raise an alert even though another "
            "warehouse is overstocked — got %s" % findings)
        self.assertEqual(findings[0]['severity'], 'critical')
        self.assertIn('WH1', findings[0]['name'])
        self.assertIn('wh-1', findings[0]['dedup_key'],
                      "warehouse must be part of the identity, or the same "
                      "product short in two warehouses collapses into one alert")

    def test_the_same_product_short_in_two_warehouses_is_two_alerts(self):
        self._stub_engine_keyed({
            'orderpoint_minimums': [
                ((7, 'Widget'), (1, 'WH1'), 100.0),
                ((7, 'Widget'), (2, 'WH2'), 100.0),
            ],
            'stock_on_hand': [],
        })
        findings = self.Detector.detect('stock_below_safety')
        self.assertEqual(len(findings), 2)
        self.assertEqual(len({f['dedup_key'] for f in findings}), 2,
                         "two distinct problems need two distinct dedup keys")

    def test_stock_scan_cursor_advances_and_wraps(self):
        """The resumable scan must move on, and must wrap rather than run off.

        Without the wrap the cursor would climb past the end of the catalogue
        and the detector would go permanently silent — a false negative that
        only appears after N runs and that no single-run test would notice.
        """
        param = self.env['ir.config_parameter'].sudo()

        # A short page (fewer rows than the page size) means "end of catalogue".
        self._stub_engine_keyed({
            'orderpoint_minimums': [((7, 'Widget'), (1, 'WH1'), 100.0)],
            'stock_on_hand': [((7, 'Widget'), (1, 'WH1'), 100.0)],
        })
        param.set_param(det.STOCK_CURSOR_PARAM, '250')
        self.Detector.detect('stock_below_safety')
        self.assertEqual(param.get_param(det.STOCK_CURSOR_PARAM), '0',
                         "a short page means the catalogue is exhausted; the "
                         "cursor must wrap to 0, not keep climbing")

        # A full page means there is more to come: advance by the page size.
        full_page = [((i, 'P%s' % i), (1, 'WH1'), 1.0)
                     for i in range(det.STOCK_PAGE_SIZE)]
        self._stub_engine_keyed({
            'orderpoint_minimums': full_page,
            'stock_on_hand': [((i, 'P%s' % i), (1, 'WH1'), 5.0)
                              for i in range(det.STOCK_PAGE_SIZE)],
        })
        param.set_param(det.STOCK_CURSOR_PARAM, '0')
        self.Detector.detect('stock_below_safety')
        self.assertEqual(param.get_param(det.STOCK_CURSOR_PARAM),
                         str(det.STOCK_PAGE_SIZE))

    def test_digest_sends_one_mail_for_the_open_alerts(self):
        """Smoke test: the digest is a cron nobody would notice failing."""
        self._stub_engine(_NORMAL + [3])
        for finding in self.Detector.detect('sales_trend_drop'):
            self.Alert._record_finding(finding)
        self.assertTrue(self.Alert.search_count([('state', '=', 'new')]))

        before = self.env['mail.mail'].search_count([])
        self.env['res.users'].browse(2).write({'email': 'admin@example.com'})
        self.Alert._cron_send_digest()
        self.assertGreater(
            self.env['mail.mail'].search_count([]), before,
            "the digest must actually produce a mail when alerts are open")

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

    # The detector's own logger is muted for the two tests below. They exist to
    # prove the code survives an exception, so `_logger.exception` firing is the
    # CORRECT behaviour — but it writes a traceback into odoo-test.log, and CI
    # fails the whole run on any traceback there ("Odoo can exit 0 on some test
    # failures"). That rule is right; deliberately-provoked failures are the
    # exception it needs, which is what mute_logger is for. Muting the specific
    # logger, not the level, so an UNEXPECTED traceback from anywhere else still
    # trips CI.
    @mute_logger(_DETECTOR_LOGGER)
    def test_a_failing_detector_cannot_sink_the_others(self):
        """ONE detector raising must not cost the others.

        An earlier version made every spec explode, so all four failed
        identically — which proved the loop survives, but not the thing the name
        claims. Here only the sales spec raises; the expense detector must still
        return its finding.
        """
        Engine = type(self.env['ncollection.aggregation.engine'])
        rows = [("2026-08-%02d" % (i + 1), v)
                for i, v in enumerate(_NORMAL + [900])]

        def selective(engine_self, spec):
            if spec['key'] == 'sales_trend':
                raise RuntimeError("simulated ORM failure")
            return {'key': spec['key'], 'rows': rows, 'cached': False}

        self.patch(Engine, 'aggregate', selective)

        self.assertEqual(self.Detector.detect('sales_trend_drop'), [],
                         "the broken detector yields nothing rather than raising")
        self.assertTrue(self.Detector.detect('expense_spike'),
                        "a healthy detector must be unaffected by its neighbour")

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

    @mute_logger(_DETECTOR_LOGGER)
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
