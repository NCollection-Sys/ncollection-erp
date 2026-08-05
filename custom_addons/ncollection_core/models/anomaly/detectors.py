# -*- coding: utf-8 -*-
"""The four anomaly detectors (P5-T04).

Every query goes through ``ncollection.aggregation.engine`` — the P4-T01 choke
point ``ARCHITECTURE_DATA_PLATFORM`` §9 mandates ("No widget queries models
directly"). That is not ceremony; it is what buys three properties for free:

* **Soft dependencies.** ``sale`` and ``hr`` are genuinely absent from many
  tenants (verified live: both uninstalled on the dev tenant while ``account``
  and ``stock`` are installed). The engine returns ``None`` for a model this
  database does not have, so the detector silently produces no findings instead
  of raising. No ``depends`` is added, so P1-T09 menu visibility and P1-T10
  licence enforcement stay untouched.
* **Ring-2 licensing, inherited not re-implemented.** A model the plan does not
  licence for this user raises ``AccessError`` inside the engine and is dropped
  there. This module never consults a plan.
* **Fail-open.** The engine never raises into a caller.

Detector shape: each returns a list of *findings* — plain dicts, no ORM — which
``ncollection.alert._record_finding`` turns into records. Keeping detection and
persistence apart is what lets the acceptance test assert on detection alone.
"""

import logging
from datetime import timedelta

from odoo import api, fields, models

from . import statistics as stats

_logger = logging.getLogger(__name__)

# How much history each statistical detector looks at. 30 daily points is long
# enough for a weekly rhythm to show up in the baseline and short enough that a
# genuine level-shift is not averaged away for a month.
BASELINE_DAYS = 30

# Registry order is the order the cron processes them in, and therefore the
# order of the batches — see `ncollection.alert._cron_detect_anomalies`.
DETECTOR_KEYS = (
    'sales_trend_drop',
    'expense_spike',
    'attendance_anomaly',
    'stock_below_safety',
)


class NCollectionAnomalyDetector(models.AbstractModel):
    """Stateless detectors. Abstract because there is nothing to persist here."""

    _name = 'ncollection.anomaly.detector'
    _description = 'NCollection Anomaly Detectors'

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @api.model
    def _window_start(self, reference=None, days=BASELINE_DAYS):
        reference = reference or fields.Date.context_today(self)
        return reference - timedelta(days=days)

    @api.model
    def _series_from_rows(self, rows):
        """Turn engine rows into a chronological list of floats.

        Engine rows are positional tuples — groupby terms first, then
        aggregates (see ``_read_group_uncached``). With one groupby and one
        aggregate that is ``(period, value)``.

        Missing periods are NOT back-filled with zeros. A day with no sales
        produces no row, and inventing a 0.0 would manufacture a crash in the
        series that never happened — turning every weekend into an anomaly.
        """
        series = []
        for row in rows or ():
            if not isinstance(row, (tuple, list)) or len(row) < 2:
                continue
            value = row[-1]
            if value is None:
                continue
            try:
                series.append(float(value))
            except (TypeError, ValueError):
                continue
        return series

    @api.model
    def _dedup_key(self, detector_key, scope, reference=None):
        """Stable per (detector, subject, day).

        The day stamp is what makes a daily cron idempotent WITHIN a day and
        still able to alert again tomorrow if the condition persists.
        """
        reference = reference or fields.Date.context_today(self)
        return '%s:%s:%s' % (detector_key, scope, reference)

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    @api.model
    def detect(self, detector_key, reference=None):
        """Run one detector. Never raises; an unknown key yields nothing."""
        method = getattr(self, '_detect_%s' % detector_key, None)
        if method is None:
            _logger.warning('No such anomaly detector: %r', detector_key)
            return []
        try:
            return method(reference=reference) or []
        except Exception:  # noqa: BLE001 - one detector must not sink the cron
            _logger.exception('Anomaly detector %r failed', detector_key)
            return []

    # ------------------------------------------------------------------
    # Detectors
    # ------------------------------------------------------------------

    @api.model
    def _detect_sales_trend_drop(self, reference=None):
        """Daily confirmed-sales total falling far below its own baseline."""
        engine = self.env['ncollection.aggregation.engine']
        result = engine.aggregate({
            'key': 'sales_trend',
            'model': 'sale.order',
            'domain': [
                ('state', 'in', ['sale', 'done']),
                ('date_order', '>=', self._window_start(reference)),
            ],
            'groupby': ['date_order:day'],
            'aggregates': ['amount_total:sum'],
            'order': 'date_order:day asc',
        })
        if not result:
            return []      # sale not installed, or not licensed for this user

        series = self._series_from_rows(result['rows'])
        zscore = stats.zscore_of_latest(series)
        # Only a DROP is interesting here. A record sales day is not an alert,
        # and reporting it as one is how a feature like this loses credibility.
        if zscore is None or zscore > 0:
            return []
        if not stats.is_anomalous(zscore):
            return []

        baseline = sum(series[:-1]) / float(len(series) - 1)
        return [{
            'detector_key': 'sales_trend_drop',
            'name': 'Sales dropped sharply against the 30-day baseline',
            'suggested_action': (
                'Check for stalled quotations, a pricing or availability '
                'problem, or a sales channel that stopped reporting.'),
            'observed_value': series[-1],
            'baseline_value': baseline,
            'zscore': zscore,
            'dedup_key': self._dedup_key('sales_trend_drop', 'all', reference),
        }]

    @api.model
    def _detect_expense_spike(self, reference=None):
        """Daily vendor-bill total spiking far above its own baseline."""
        engine = self.env['ncollection.aggregation.engine']
        result = engine.aggregate({
            'key': 'expense_trend',
            'model': 'account.move',
            'domain': [
                ('move_type', '=', 'in_invoice'),
                ('state', '=', 'posted'),
                ('invoice_date', '>=', self._window_start(reference)),
            ],
            'groupby': ['invoice_date:day'],
            'aggregates': ['amount_total:sum'],
            'order': 'invoice_date:day asc',
        })
        if not result:
            return []

        series = self._series_from_rows(result['rows'])
        zscore = stats.zscore_of_latest(series)
        # Only a SPIKE. Spending less than usual is not an incident.
        if zscore is None or zscore < 0:
            return []
        if not stats.is_anomalous(zscore):
            return []

        baseline = sum(series[:-1]) / float(len(series) - 1)
        return [{
            'detector_key': 'expense_spike',
            'name': 'Vendor bills spiked well above the 30-day baseline',
            'suggested_action': (
                'Review today\'s posted vendor bills for duplicates, a '
                'mis-keyed amount, or an unplanned purchase.'),
            'observed_value': series[-1],
            'baseline_value': baseline,
            'zscore': zscore,
            'dedup_key': self._dedup_key('expense_spike', 'all', reference),
        }]

    @api.model
    def _detect_attendance_anomaly(self, reference=None):
        """Daily attendance check-in count deviating from its baseline."""
        engine = self.env['ncollection.aggregation.engine']
        result = engine.aggregate({
            'key': 'attendance_trend',
            'model': 'hr.attendance',
            'domain': [('check_in', '>=', self._window_start(reference))],
            'groupby': ['check_in:day'],
            'aggregates': ['__count'],
            'order': 'check_in:day asc',
        })
        if not result:
            return []      # hr not installed on this tenant — the common case

        series = self._series_from_rows(result['rows'])
        zscore = stats.zscore_of_latest(series)
        if not stats.is_anomalous(zscore):
            return []

        baseline = sum(series[:-1]) / float(len(series) - 1)
        direction = 'below' if zscore < 0 else 'above'
        return [{
            'detector_key': 'attendance_anomaly',
            'name': 'Attendance check-ins well %s the 30-day baseline' % direction,
            'suggested_action': (
                'Confirm whether this is a holiday, a shift change, or a '
                'terminal that stopped recording.'),
            'observed_value': series[-1],
            'baseline_value': baseline,
            'zscore': zscore,
            'dedup_key': self._dedup_key('attendance_anomaly', 'all', reference),
        }]

    @api.model
    def _detect_stock_below_safety(self, reference=None):
        """Products held below their configured reordering minimum.

        Threshold, not statistics: being under a minimum somebody configured is
        a fact about now, not a deviation from history. It supplies `severity`
        directly rather than a z-score — see `_record_finding`.
        """
        engine = self.env['ncollection.aggregation.engine']
        minimums = engine.aggregate({
            'key': 'orderpoint_minimums',
            'model': 'stock.warehouse.orderpoint',
            'groupby': ['product_id'],
            'aggregates': ['product_min_qty:max'],
        })
        if not minimums:
            return []      # stock not installed, or no reordering rules

        on_hand = engine.aggregate({
            'key': 'stock_on_hand',
            'model': 'stock.quant',
            'domain': [('location_id.usage', '=', 'internal')],
            'groupby': ['product_id'],
            'aggregates': ['quantity:sum'],
        })
        quantities = {}
        for row in (on_hand or {}).get('rows') or ():
            if isinstance(row, (tuple, list)) and len(row) >= 2 and row[0]:
                product_id = row[0][0] if isinstance(row[0], (tuple, list)) else row[0]
                quantities[product_id] = float(row[-1] or 0.0)

        findings = []
        for row in minimums['rows'] or ():
            if not isinstance(row, (tuple, list)) or len(row) < 2 or not row[0]:
                continue
            # groupby many2one cells are flattened to (id, label) by the engine.
            product = row[0]
            product_id, label = (product if isinstance(product, (tuple, list))
                                 else (product, str(product)))
            minimum = float(row[-1] or 0.0)
            if minimum <= 0:
                continue
            available = quantities.get(product_id, 0.0)
            if available >= minimum:
                continue

            # Severity by depth of the shortfall: out of stock is materially
            # worse than slightly under the reorder point.
            shortfall_ratio = (minimum - available) / minimum
            if available <= 0:
                severity = 'critical'
            elif shortfall_ratio >= 0.5:
                severity = 'warning'
            else:
                severity = 'info'

            findings.append({
                'detector_key': 'stock_below_safety',
                'name': '%s is below its safety stock level' % label,
                'severity': severity,
                'suggested_action': (
                    'On hand %.2f against a minimum of %.2f — raise a purchase '
                    'order or review the reordering rule.' % (available, minimum)),
                'observed_value': available,
                'baseline_value': minimum,
                'zscore': 0.0,
                'dedup_key': self._dedup_key(
                    'stock_below_safety', 'product-%s' % product_id, reference),
            })
        return findings
