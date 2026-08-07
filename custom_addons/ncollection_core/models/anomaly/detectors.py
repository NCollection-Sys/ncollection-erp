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
* **Module availability, inherited not re-implemented.** The engine's registry
  check drops a model this database does not have, and this module never
  consults a plan of its own.

  **Ring-2 licensing IS enforced on the cron path (#347).** It was not, and the
  previous text here said so plainly rather than papering over it. The cause:
  `ir.cron` rows carried no explicit ``user_id``, so they ran as the superuser
  (verified — every cron in a live tenant resolved to ``user_id = 1``), and
  ``env.su`` bypasses the ENTIRE access-check machinery. Not merely
  ``license_enforcement``'s own exemption: Odoo's ``check_access``,
  ``has_access`` and ``_filtered_access`` all short-circuit on ``env.su``
  *before* any override is reached, and ``search()`` ignores ACLs outright. So
  the probe this engine uses to detect a Ring-2 denial could never fail.

  That is why the fix is an IDENTITY, not a flag: the crons now run as
  ``ncollection_core.user_cron_service``, a plain internal user (deliberately
  not ``base.group_system``, which is exempt too). What a detector can read now
  tracks the tenant's plan automatically, through the same path real users take
  — licensing inherited, never re-implemented. A tenant that downgrades off HR
  stops getting attendance findings even while ``hr`` remains installed.

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

# How many (product, warehouse) reordering rules one run of the stock detector
# looks at. This is the only detector whose cost scales with catalogue size
# rather than with a date window, and a tenant DB has no queue runner to hand
# long work to — so it is paged, and the page is what bounds a single cron run.
#
# 500 is chosen to be comfortably sub-second on the P4-T01 performance budget
# (every dashboard endpoint under 500ms on 100k-record demo data) while still
# covering an ordinary SMB catalogue in a single run. A larger catalogue is
# covered across successive runs by the cursor below, so the page size trades
# LATENCY for safety, never coverage.
STOCK_PAGE_SIZE = 500

# Where the resumable scan remembers it got to. An ir.config_parameter rather
# than a column, because it is one integer of tenant-local scheduler state and
# does not belong on a business record.
STOCK_CURSOR_PARAM = 'ncollection_core.anomaly_stock_offset'

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
    def _cell_id(self, cell):
        """Id out of an engine groupby cell.

        The engine flattens a grouped many2one to ``(id, label)`` — including
        the null group, which becomes ``(False, '')``. One shape, always, so
        this never has to special-case "present but empty".
        """
        if isinstance(cell, (tuple, list)) and cell:
            return cell[0], (cell[1] if len(cell) > 1 else '')
        return cell, str(cell)

    @api.model
    def _detect_stock_below_safety(self, reference=None):
        """Products below their reordering minimum, PER WAREHOUSE.

        Threshold, not statistics: being under a minimum somebody configured is
        a fact about now, not a deviation from history, so this supplies
        `severity` directly rather than a z-score (see `_record_finding`).

        **Grouped by (product, warehouse), not by product.** An earlier version
        grouped both sides by ``product_id`` alone, which silently masked real
        stockouts: ``product_min_qty:max`` collapsed every warehouse's rule into
        one number while ``stock.quant`` was summed across ALL internal
        locations, so a product completely out of stock in Warehouse 1 was
        invisible whenever Warehouse 2 happened to hold enough. Reproduced on a
        live database before this fix (min 100 / on-hand 0 in one warehouse,
        110 in another -> no finding at all). `stock.warehouse.orderpoint` is
        warehouse-scoped by definition, so the comparison has to be too.

        **Bounded and resumable.** This is the one detector whose cost scales
        with catalogue size rather than with a date window, and a tenant
        database has no queue runner to escalate long work to. An unbounded
        ``stock.quant`` read here would hold a shared `odoo-bus` cron thread for
        as long as it took — starving every OTHER tenant's crons, which is #310
        multiplied by tenant count. So it reads one page per run and remembers
        where it stopped; successive runs cover the whole catalogue without any
        single run being unbounded.
        """
        engine = self.env['ncollection.aggregation.engine']
        offset = self._stock_cursor()

        minimums = engine.aggregate({
            'key': 'orderpoint_minimums',
            'model': 'stock.warehouse.orderpoint',
            'groupby': ['product_id', 'warehouse_id'],
            'aggregates': ['product_min_qty:max'],
            'limit': STOCK_PAGE_SIZE,
            'offset': offset,
        })
        if not minimums:
            return []      # stock not installed, or no reordering rules

        rows = minimums['rows'] or []
        # Advance (or wrap) the cursor BEFORE any early return, so a page that
        # produces no findings still moves on rather than pinning the scan.
        self._advance_stock_cursor(offset, len(rows))
        if not rows:
            return []

        product_ids = []
        for row in rows:
            if isinstance(row, (tuple, list)) and len(row) >= 3:
                product_id, _label = self._cell_id(row[0])
                if product_id:
                    product_ids.append(product_id)
        if not product_ids:
            return []

        # Scoped to the page's products, so this read is bounded by the page
        # rather than by the size of the tenant's inventory.
        on_hand = engine.aggregate({
            'key': 'stock_on_hand',
            'model': 'stock.quant',
            'domain': [
                ('location_id.usage', '=', 'internal'),
                ('product_id', 'in', product_ids),
            ],
            'groupby': ['product_id', 'warehouse_id'],
            'aggregates': ['quantity:sum'],
        })

        quantities = {}
        for row in (on_hand or {}).get('rows') or ():
            if not isinstance(row, (tuple, list)) or len(row) < 3:
                continue
            product_id, _ = self._cell_id(row[0])
            warehouse_id, _ = self._cell_id(row[1])
            quantities[(product_id, warehouse_id)] = float(row[-1] or 0.0)

        findings = []
        for row in rows:
            if not isinstance(row, (tuple, list)) or len(row) < 3:
                continue
            product_id, product_label = self._cell_id(row[0])
            warehouse_id, warehouse_label = self._cell_id(row[1])
            if not product_id:
                continue
            minimum = float(row[-1] or 0.0)
            if minimum <= 0:
                continue

            # Absent from the quant read means no stock in that warehouse at
            # all — 0.0, which is the most severe case, not a reason to skip.
            available = quantities.get((product_id, warehouse_id), 0.0)
            if available >= minimum:
                continue

            # Severity by depth of the shortfall: out of stock is materially
            # worse than slightly under the reorder point.
            if available <= 0:
                severity = 'critical'
            elif (minimum - available) / minimum >= 0.5:
                severity = 'warning'
            else:
                severity = 'info'

            where = ' in %s' % warehouse_label if warehouse_label else ''
            findings.append({
                'detector_key': 'stock_below_safety',
                'name': '%s is below its safety stock level%s' % (
                    product_label, where),
                'severity': severity,
                'suggested_action': (
                    'On hand %.2f against a minimum of %.2f — raise a purchase '
                    'order or review the reordering rule.' % (available, minimum)),
                'observed_value': available,
                'baseline_value': minimum,
                'zscore': 0.0,
                # Warehouse is part of the identity: the same product short in
                # two warehouses is two problems, not one.
                'dedup_key': self._dedup_key(
                    'stock_below_safety',
                    'product-%s-wh-%s' % (product_id, warehouse_id),
                    reference),
            })
        return findings

    # ------------------------------------------------------------------
    # Resumable scan cursor
    # ------------------------------------------------------------------

    @api.model
    def _stock_cursor(self):
        value = self.env['ir.config_parameter'].sudo().get_param(
            STOCK_CURSOR_PARAM, '0')
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    @api.model
    def _advance_stock_cursor(self, offset, row_count):
        """Move to the next page, or wrap when the catalogue is exhausted.

        Wrapping on a short page is what makes the scan cyclic: every product
        is revisited on a later run, so a bounded page size costs latency, not
        coverage. Without the wrap the cursor would run off the end and the
        detector would go permanently silent — a false negative that no test
        would notice, because it only appears after N runs.
        """
        next_offset = 0 if row_count < STOCK_PAGE_SIZE else offset + row_count
        self.env['ir.config_parameter'].sudo().set_param(
            STOCK_CURSOR_PARAM, str(next_offset))
