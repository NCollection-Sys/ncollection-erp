# -*- coding: utf-8 -*-
"""F4-T01: the three FINANCIAL KPIs, the ones ncollection_core declined.

``ncollection_core/models/kpi/kpi.py`` says so in its own docstring: *"the
FINANCIAL KPIs (Revenue Growth %, DSO, Gross Margin %) belong to
``ncollection_account_analytics`` (#120)… This module keeps only the OPERATIONAL
three."* Core went as far as making its Inventory Turnover QUANTITY-based rather
than the textbook COGS-based formula, specifically to stay off this line. This
file is the other side of that split, so it deliberately mirrors core's shape —
``key`` Selection dispatching to ``_compute_<key>``, a period plus the one
before, ``value=None`` meaning "cannot answer" — and a dashboard can render
either model with the same code. The payload KEYS match exactly; one field's
semantics deliberately do not, and ``_nc_delta_pct`` says which and why.

WHY THIS READS ``account.move.line`` DIRECTLY. ARCHITECTURE_DATA_PLATFORM §9.4
makes the aggregation engine the choke point for **dashboard queries** and says
"no widget queries models directly". This is not a widget; it is the service a
widget calls, exactly as ``ncollection_account_reports`` exposes ``_nc_totals()``
to ``ncollection_account_dashboard`` today. Routing financial computation
through an engine that documents "aggregation only — never financial
computation" would put the meaning of a number in the layer that says it does
not decide what numbers mean.

DEFINITIONS ARE STATED, NOT ASSUMED. Each KPI below has more than one textbook
formula. The one implemented is written out in its docstring with the reason,
because a KPI whose definition lives only in code is a number nobody can audit —
and a finance lead who disagrees needs to see the choice, not reverse-engineer
it.
"""
import logging
from datetime import date as date_cls, timedelta

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

KPI_KEYS = [
    ('revenue_growth', 'Revenue Growth %'),
    ('dso', 'Days Sales Outstanding'),
    ('gross_margin', 'Gross Margin %'),
]

# Same vocabulary as ncollection.kpi, so ncollection_account_dashboard's
# `_KPI_UNIT` map renders these without a special case. 'days' is new and maps
# to a plain number there, which is what a day count is.
UNITS = [
    ('currency', 'Currency'),
    ('percent', 'Percent'),
    ('days', 'Days'),
]

PERIOD_TYPES = [
    ('month', 'Month'),
    ('quarter', 'Quarter'),
    ('year', 'Year'),
]

# Odoo account types, by role in these formulas. Grouped here rather than
# inline for the same reason the Balance Sheet groups its buckets: this is the
# judgement a reviewer needs to see, and #411 is what happens when such a map is
# edited without one place to check it.
REVENUE_TYPES = ('income', 'income_other')
COGS_TYPES = ('expense_direct_cost',)
RECEIVABLE_TYPES = ('asset_receivable',)


class NcollectionFinancialKpi(models.Model):
    """One row per ``key``, and the three rows are canonical.

    ACL (``security/ir.model.access.csv``) gives an accounting manager
    read+write but NOT create or unlink. A fourth row could name no
    ``_compute_<key>`` method and would silently compute nothing, and the views
    already declare ``create="false" delete="false"`` — an ACL that out-grants
    its own UI is exactly the gap RPC finds. What a manager legitimately owns is
    the target and the period, which write covers.

    (This note lives here rather than in the CSV: Odoo parses that file with its
    CSV importer, which has no comment syntax — a ``#`` line is read as a data
    row and fails the whole registry load.)
    """

    _name = 'ncollection.account.analytics.kpi'
    _description = 'NCollection Financial KPI'
    _order = 'sequence, id'

    key = fields.Selection(KPI_KEYS, required=True, index=True)
    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    unit = fields.Selection(UNITS, required=True, default='percent')
    period_type = fields.Selection(PERIOD_TYPES, required=True, default='month')
    target_value = fields.Float()

    _key_uniq = models.Constraint(
        'unique(key)',
        'One definition per financial KPI key.',
    )

    # ---- period windows (same half-open convention as ncollection.kpi) ----

    @api.model
    def _nc_period_bounds(self, period_type, reference=None):
        """``(start, end)`` of the period CONTAINING ``reference``, half-open.

        ``[start, end)`` so an entry dated exactly on a boundary belongs to
        exactly one period — the classic off-by-one in period comparison, and
        the reason this matches ``ncollection.kpi._period_bounds`` rather than
        inventing a second convention.
        """
        reference = reference or fields.Date.context_today(self)
        if isinstance(reference, str):
            reference = fields.Date.to_date(reference)
        if period_type == 'year':
            start = date_cls(reference.year, 1, 1)
            end = start + relativedelta(years=1)
        elif period_type == 'quarter':
            first_month = 3 * ((reference.month - 1) // 3) + 1
            start = date_cls(reference.year, first_month, 1)
            end = start + relativedelta(months=3)
        else:
            start = reference.replace(day=1)
            end = start + relativedelta(months=1)
        return start, end

    @api.model
    def _nc_previous_bounds(self, period_type, start):
        delta = {'year': relativedelta(years=1),
                 'quarter': relativedelta(months=3),
                 'month': relativedelta(months=1)}[period_type]
        return start - delta, start

    # ---- public API -------------------------------------------------------

    def compute(self, reference=None):
        """This KPI for its period plus the one before.

        ``value is None`` means "cannot be computed on this tenant" — a
        dashboard renders nothing rather than a misleading zero, matching
        ``ncollection.kpi.compute()``'s contract exactly.
        """
        self.ensure_one()
        start, end = self._nc_period_bounds(self.period_type, reference)
        prev_start, prev_end = self._nc_previous_bounds(self.period_type, start)

        method = getattr(self, '_compute_%s' % self.key, None)
        if method is None:  # pragma: no cover - guarded by the Selection
            _logger.error('No computation method for financial KPI %r', self.key)
            return self._nc_result(None, None, start, end)
        return self._nc_result(method(start, end), method(prev_start, prev_end),
                               start, end)

    def _nc_result(self, value, previous, start, end):
        return {
            'key': self.key,
            'name': self.name,
            'unit': self.unit,
            'value': value,
            'previous': previous,
            'delta_pct': self._nc_delta_pct(value, previous),
            'target': self.target_value,
            'period_start': start,
            'period_end': end,
        }

    @api.model
    def _nc_delta_pct(self, value, previous):
        """Percentage change, or ``None`` when it is not defined.

        ``None`` rather than 0.0 when the previous period is zero or missing:
        "no comparison available" and "no change" are different statements and a
        dashboard that renders them identically tells a lie. Copied in intent
        from ``ncollection.kpi._delta_pct``.

        NOTE for the two percentage KPIs: this is a change in the RATE, not
        percentage points. A reader who wants points wants ``value - previous``.

        DIVERGES DELIBERATELY from ``ncollection.kpi._delta_pct``, which divides
        by a SIGNED ``previous``. Over a signed denominator a figure worsening
        from -100 to -150 reports +50%, reading as an improvement. The two
        models therefore share a payload SHAPE but not this field's semantics
        whenever ``previous`` is negative — a renderer that assumes one
        convention will disagree with the other model, so the divergence is
        stated here rather than discovered.
        """
        if value is None or previous is None or not previous:
            return None
        return (value - previous) / abs(previous) * 100.0

    # ---- shared figure helpers -------------------------------------------

    @api.model
    def _nc_movement(self, account_types, date_from, date_to, company=None):
        """Signed balance movement over ``account_types`` in a window.

        Posted entries only — a draft invoice is an intention, and a KPI that
        counted it would move when nothing had happened.

        ``company`` defaults to the active one and exists so this matches
        ``dimension._nc_breakdown``'s signature: two sibling services in one
        module where only one can be pointed at another company is an asymmetry
        the first cross-company caller discovers, not the author.
        """
        company = company or self.env.company
        rows = self.env['account.move.line']._read_group(
            [('parent_state', '=', 'posted'),
             ('company_id', '=', company.id),
             ('account_id.account_type', 'in', list(account_types)),
             ('date', '>=', date_from), ('date', '<', date_to)],
            aggregates=['balance:sum'])
        return (rows[0][0] or 0.0) if rows else 0.0

    @api.model
    def _nc_closing(self, account_types, date, company=None):
        """Cumulative balance on ``account_types`` up to and including ``date``.

        A position, not a flow — receivables outstanding is "what is owed now",
        which is every posting since the beginning of time, not this month's.
        """
        company = company or self.env.company
        rows = self.env['account.move.line']._read_group(
            [('parent_state', '=', 'posted'),
             ('company_id', '=', company.id),
             ('account_id.account_type', 'in', list(account_types)),
             ('date', '<=', date)],
            aggregates=['balance:sum'])
        return (rows[0][0] or 0.0) if rows else 0.0

    @api.model
    def _nc_revenue(self, date_from, date_to, company=None):
        """Revenue for the window, presented positive.

        Income accounts are credit-normal, so their balance movement is
        negative when the business earns money; negating is what makes "revenue
        of 1,000" read as 1,000. Same convention as the P&L's `sign=-1`.
        """
        return -self._nc_movement(REVENUE_TYPES, date_from, date_to,
                                  company=company)

    # ---- the KPIs ---------------------------------------------------------

    def _compute_revenue_growth(self, start, end):
        """Growth of this window's revenue over the window immediately before.

            (revenue(window) - revenue(prior)) / |revenue(prior)| * 100

        ABSOLUTE denominator on purpose: over a signed one, a business whose
        revenue went from -100 (net of refunds) to -150 would report +50%
        growth — the opposite of what happened. The report engine's
        ``_nc_variance`` negates the same trap for the same reason.

        ``None`` when the prior window had no revenue: growth from nothing is
        not 0%, not infinite, and not a number a dashboard should show.

        Because the VALUE is itself a growth rate, ``compute()``'s ``previous``
        is the prior window's growth rate and ``delta_pct`` therefore describes
        whether growth accelerated. That is deliberate and documented rather
        than hidden.
        """
        prior_start, _prior_end = self._nc_previous_bounds(self.period_type, start)
        current = self._nc_revenue(start, end)
        prior = self._nc_revenue(prior_start, start)
        if not prior:
            return None
        return (current - prior) / abs(prior) * 100.0

    def _compute_gross_margin(self, start, end):
        """Gross margin as a percentage of revenue.

            (revenue - cost of sales) / revenue * 100

        Cost of sales is ``expense_direct_cost`` only — the account type Odoo
        reserves for it. Operating expenses are deliberately NOT included: that
        would be net margin, a different KPI with the same name in careless
        dashboards.

        ``None`` when there is no revenue: a margin on nothing is undefined,
        and 0% would read as "we sold at cost".
        """
        revenue = self._nc_revenue(start, end)
        if not revenue:
            return None
        cogs = self._nc_movement(COGS_TYPES, start, end)
        return (revenue - cogs) / revenue * 100.0

    def _compute_dso(self, start, end):
        """Days Sales Outstanding — how long revenue sits unpaid.

            (receivables at period end / revenue in period) * days in period

        THE SIMPLE (single-period) FORMULA, chosen deliberately over the
        countback method. Countback is more accurate for seasonal books but
        needs an arbitrary look-back depth, and a KPI whose value depends on a
        hidden tuning constant is one nobody can reproduce by hand. This one a
        finance lead can check on a napkin, which is the property that matters
        for a dashboard number.

        ``None`` when the window has no revenue — dividing by it would produce
        an infinity dressed as a day count.
        """
        revenue = self._nc_revenue(start, end)
        if not revenue:
            return None
        # `end` is exclusive, so the last day actually inside the window is the
        # day before it — using `end` would read one day of postings that
        # belong to the next period.
        last_day = end - timedelta(days=1)
        receivables = self._nc_closing(RECEIVABLE_TYPES, last_day)
        return (receivables / revenue) * (end - start).days
