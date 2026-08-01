# -*- coding: utf-8 -*-
"""Operational KPI definitions (P4-T02).

Three KPIs, each with a computation method, a period comparison, and threshold
bands. Consumed by the Phase-4 dashboards (#56 / #57).

**Scope is narrower than DELIVERABLE_1's row.** That row lists six KPIs, but
issue #55 carries an Architecture Alignment note recording a split already
executed: the FINANCIAL KPIs (Revenue Growth %, DSO, Gross Margin %) belong to
``ncollection_account_analytics`` (#120), per FINANCIAL_PLATFORM_ARCHITECTURE's
"Financial KPIs" ownership and ADR #15. This module keeps only the OPERATIONAL
three. The issue title says so too: "Operational KPI Logic Models".

That boundary is why Inventory Turnover here is QUANTITY-based rather than the
textbook ``COGS / average inventory value``: COGS is a financial figure, and
computing it here would put this module straight back over the line the split
just moved it off.

**Every read goes through the aggregation engine** (#54), never a direct
``_read_group`` — ARCHITECTURE_DATA_PLATFORM §9 makes it the single choke point
for dashboard queries. That also buys soft dependencies for free: a tenant
without ``hr`` installed, or a user whose plan does not license ``stock``, gets
no value for that KPI instead of an error, because the engine drops a spec whose
model is absent or unreadable.

**Computation is a closed set.** ``key`` is a Selection dispatching to a named
``_compute_<key>`` method. There is deliberately no free-text SQL/Python field:
the acceptance criterion is "KPI values match hand-calculated fixtures exactly",
which requires deterministic, unit-testable code, and admin-authored code in a
multi-tenant product is a surface nobody asked for.
"""

import logging
from datetime import date

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

KPI_KEYS = [
    ('avg_deal_size', 'Average Deal Size'),
    ('employee_turnover', 'Employee Turnover'),
    ('inventory_turnover', 'Inventory Turnover'),
]

UNITS = [
    ('currency', 'Currency'),
    ('percent', 'Percent'),
    ('ratio', 'Ratio'),
]

PERIOD_TYPES = [
    ('month', 'Month'),
    ('quarter', 'Quarter'),
    ('year', 'Year'),
]


class NCollectionKpi(models.Model):
    _name = 'ncollection.kpi'
    _description = 'NCollection Operational KPI'
    _order = 'sequence, id'

    key = fields.Selection(KPI_KEYS, required=True, index=True)
    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    unit = fields.Selection(UNITS, required=True, default='ratio')
    period_type = fields.Selection(PERIOD_TYPES, required=True, default='month')
    target_value = fields.Float()
    threshold_ids = fields.One2many('ncollection.kpi.threshold', 'kpi_id')

    _key_uniq = models.Constraint(
        'unique(key)',
        'One definition per KPI key.',
    )

    # ------------------------------------------------------------------
    # Period windows
    # ------------------------------------------------------------------

    @api.model
    def _period_bounds(self, period_type, reference=None):
        """``(start, end)`` of the period CONTAINING ``reference``.

        Half-open ``[start, end)`` so a record dated exactly on a boundary
        belongs to exactly one period — the classic source of off-by-one in
        period comparisons.
        """
        reference = reference or fields.Date.context_today(self)
        if isinstance(reference, str):
            reference = fields.Date.to_date(reference)
        if period_type == 'year':
            start = date(reference.year, 1, 1)
            end = start + relativedelta(years=1)
        elif period_type == 'quarter':
            first_month = 3 * ((reference.month - 1) // 3) + 1
            start = date(reference.year, first_month, 1)
            end = start + relativedelta(months=3)
        else:
            start = reference.replace(day=1)
            end = start + relativedelta(months=1)
        return start, end

    @api.model
    def _previous_bounds(self, period_type, start):
        """The window immediately before the one starting at ``start``."""
        delta = {
            'year': relativedelta(years=1),
            'quarter': relativedelta(months=3),
            'month': relativedelta(months=1),
        }[period_type]
        return start - delta, start

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compute(self, reference=None):
        """Compute this KPI for its period plus the one before.

        Returns ``{key, name, unit, value, previous, delta_pct, band, target,
        period_start, period_end}``. ``value`` is None when the KPI cannot be
        computed on this tenant (app not installed, or not licensed for this
        user) — a dashboard renders nothing rather than a misleading zero.
        """
        self.ensure_one()
        start, end = self._period_bounds(self.period_type, reference)
        prev_start, prev_end = self._previous_bounds(self.period_type, start)

        method = getattr(self, '_compute_%s' % self.key, None)
        if method is None:  # pragma: no cover - guarded by the Selection
            _logger.error('No computation method for KPI key %r', self.key)
            return self._result(None, None, start, end)

        value = method(start, end)
        previous = method(prev_start, prev_end)
        return self._result(value, previous, start, end)

    def _result(self, value, previous, start, end):
        band = self.env['ncollection.kpi.threshold']._match(
            self.threshold_ids, value)
        return {
            'key': self.key,
            'name': self.name,
            'unit': self.unit,
            'value': value,
            'previous': previous,
            'delta_pct': self._delta_pct(value, previous),
            'band': band.state if band else None,
            'band_name': band.name if band else None,
            'target': self.target_value,
            'period_start': start,
            'period_end': end,
        }

    @api.model
    def _delta_pct(self, value, previous):
        """Percentage change, or None when it is not defined.

        None rather than 0.0 when the previous period is zero or missing:
        "no comparison available" and "no change" are different statements, and
        a dashboard that renders them identically tells a lie.
        """
        if value is None or previous is None or not previous:
            return None
        return (value - previous) / previous * 100.0

    # ------------------------------------------------------------------
    # Engine access
    # ------------------------------------------------------------------

    @api.model
    def _engine(self):
        return self.env['ncollection.aggregation.engine']

    @api.model
    def _single(self, spec):
        """Run one spec and return its single result row, or None.

        None propagates "this tenant cannot answer that" all the way to the
        dashboard, which is the engine's documented contract for an absent or
        unlicensed model.
        """
        return self._single_ctx(None, spec)

    @api.model
    def _single_ctx(self, context, spec):
        """``_single`` with an extra context applied to the engine's env."""
        engine = self._engine()
        if context:
            engine = engine.with_context(**context)
        result = engine.aggregate(spec)
        if not result or not result['rows']:
            return None
        return result['rows'][0]

    # ------------------------------------------------------------------
    # Computations — one per key, all hand-checkable
    # ------------------------------------------------------------------

    def _compute_avg_deal_size(self, start, end):
        """Mean value of a confirmed sales order in the window.

            SUM(sale.order.amount_total) / COUNT(sale.order)

        Confirmed orders only (``state = 'sale'``), matching the existing
        P1-T17 ``sales_this_month`` provider — counting quotations would mix
        intent with revenue.
        """
        row = self._single({
            'key': 'kpi:avg_deal_size',
            'model': 'sale.order',
            'domain': [('state', '=', 'sale'),
                       ('date_order', '>=', start),
                       ('date_order', '<', end)],
            'aggregates': ['amount_total:sum', '__count'],
        })
        if row is None:
            return None
        total, count = row[0] or 0.0, row[1] or 0
        return (total / count) if count else 0.0

    def _compute_employee_turnover(self, start, end):
        """Percentage of the workforce that left during the window.

            departures / average_headcount * 100

        ``departures``    employees whose ``departure_date`` falls in the window.
        ``headcount(D)``  employees created on or before D who had not left by D.
        ``average``       (headcount(start) + headcount(end)) / 2.

        ``departure_date`` lives on ``hr.version`` in Odoo 19, not
        ``hr.employee`` — but ``hr.employee`` delegates to it
        (``_inherits = {'hr.version': 'version_id'}``), so it is reachable
        directly in a domain. It is group-gated behind ``hr.group_hr_user``,
        so a user without HR rights gets None here rather than a wrong number.
        """
        # active_test=False is REQUIRED here, not just for the headcount below.
        # Odoo archives an employee when they leave, so the default active-only
        # filter excludes every departure — this query would find nothing and
        # the KPI would report 0.0 turnover forever, on every tenant, as a
        # confident number. Caught by test_turnover_matches_a_hand_calculated_roster;
        # the earlier tautological version of that test agreed with the bug.
        departures = self._single_ctx(
            {'active_test': False},
            {
                'key': 'kpi:turnover_departures',
                'model': 'hr.employee',
                'domain': [('departure_date', '>=', start),
                           ('departure_date', '<', end)],
                'aggregates': ['__count'],
            })
        if departures is None:
            return None

        headcount_start = self._headcount_at(start)
        headcount_end = self._headcount_at(end)
        if headcount_start is None or headcount_end is None:
            return None

        average = (headcount_start + headcount_end) / 2.0
        if not average:
            return 0.0
        return (departures[0] or 0) / average * 100.0

    def _headcount_at(self, moment):
        """Employees on the books at ``moment``: hired by then, not yet gone.

        ``active_test=False`` is essential: Odoo archives an employee when they
        leave, so the default active-only filter would hide exactly the people
        this count needs. It is set on the ENGINE's env rather than passed in
        the spec — the spec has no context field, and an unknown key would be
        silently ignored, giving a quietly wrong headcount.

        The engine folds ``active_test`` into its cache key (see
        ``context_fingerprint``), so this cannot collide with an active-only
        count of the same model.
        """
        row = self._single_ctx(
            {'active_test': False},
            {
                'key': 'kpi:headcount',
                'model': 'hr.employee',
                'domain': ['&',
                           ('create_date', '<', moment),
                           '|',
                           ('departure_date', '=', False),
                           ('departure_date', '>=', moment)],
                'aggregates': ['__count'],
            })
        return None if row is None else (row[0] or 0)

    def _compute_inventory_turnover(self, start, end):
        """How many times stock turned over, BY QUANTITY.

            units_shipped_in_window / units_on_hand

        Quantity-based on purpose. The textbook ratio is
        ``COGS / average inventory value``, but COGS is a financial figure and
        FINANCIAL_PLATFORM_ARCHITECTURE assigns financial analysis to
        ``ncollection_account_analytics`` (#120). The 2026-07-19 split kept this
        KPI here as operational, so it must be computable without touching an
        accounting model.

        ``units_on_hand`` is a POINT-IN-TIME reading of internal locations, not
        a true period average — ``stock.quant`` holds only current state, and
        reconstructing historical on-hand would mean replaying moves. Documented
        rather than hidden; a true average is a follow-up if a dashboard needs it.
        """
        shipped = self._single({
            'key': 'kpi:units_shipped',
            'model': 'stock.move',
            'domain': [('state', '=', 'done'),
                       ('location_dest_id.usage', '=', 'customer'),
                       ('date', '>=', start),
                       ('date', '<', end)],
            'aggregates': ['quantity:sum'],
        })
        if shipped is None:
            return None

        on_hand = self._single({
            'key': 'kpi:units_on_hand',
            'model': 'stock.quant',
            'domain': [('location_id.usage', '=', 'internal')],
            'aggregates': ['quantity:sum'],
        })
        if on_hand is None:
            return None

        available = on_hand[0] or 0.0
        if not available:
            return 0.0
        return (shipped[0] or 0.0) / available
