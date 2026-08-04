# -*- coding: utf-8 -*-
"""Dashboard payload service (F3-T01) — orchestration/presentation ONLY.

This is the single server entry point the Finance / Accountant / Cash OWL
dashboards call. It does NOT compute anything financial: every figure is read
from an F2-T08 executive report service
(``ncollection.account.report.*._nc_service_figures*``). This module never
touches ``account.move`` / ``account.move.line``, runs no SQL, and does no
balance / P&L math — see FPA §7 ("Must Never Own: Report Generation, Accounting
Rules") and ``tests/test_boundary.py``, which fails the build if that ever
changes.

The returned ``{kpis, charts, meta}`` shape is the STABLE contract downstream
dashboards (#56 CEO, #57 department) consume via the same base — keep it
additive. Trend direction is derived in the OWL layer from ``value`` vs
``previous`` so that even the arithmetic of a delta lives outside this Python.
"""

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models

# The executive services (F2-T08) this module consumes. Named once so the
# boundary test can assert the module only reaches the service layer.
_SUMMARY = 'ncollection.account.report.summary'
_PROFITABILITY = 'ncollection.account.report.profitability'

# P4-T01 aggregation engine (#54). The CEO dashboard is the first here to need
# NON-financial figures — pipeline and customers — and this is the only route to
# them. It is also why no `sale`/`crm` manifest dependency is added: aggregate()
# returns None (never raises) when a model is absent from the registry or Ring 2
# denies it, so a Basic-plan tenant degrades to an empty panel instead of a
# broken dashboard. See _cross_domain() below.
_AGGREGATION = 'ncollection.aggregation.engine'

_TREND_MONTHS = 6

# How many customers the "top customers" panel shows.
_TOP_CUSTOMERS = 5


class AccountDashboardService(models.AbstractModel):
    _name = 'ncollection.account.dashboard.service'
    _description = 'NCollection Account Dashboard Payload Service'

    # ---- shared orchestration helpers (reused by #56/#57) ----------------

    def _service(self, model_name):
        """An in-memory executive report record on this user's default period
        (YTD, previous-period comparison). ``new()`` — never hits the DB."""
        return self.env[model_name].new({})

    def _comparison(self, model_name):
        """``(current, previous)`` figure dicts straight from the service."""
        return self._service(model_name)._nc_service_comparison()

    @staticmethod
    def _kpi(key, label, current, previous, unit='currency'):
        """One KPI entry. No arithmetic — value/previous are raw service reads;
        the view computes the trend arrow from the two."""
        return {
            'key': key,
            'label': label,
            'value': current.get(key),
            'previous': previous.get(key),
            'unit': unit,
        }

    def _meta(self, period_from=None, period_to=None):
        company = self.env.company
        report = self._service(_SUMMARY)
        return {
            'currency': {
                'symbol': company.currency_id.symbol or company.currency_id.name,
                'position': company.currency_id.position,
            },
            'period': {
                'from': (period_from or report.date_from),
                'to': (period_to or report.date_to),
            },
            'as_of': (period_to or report.date_to),
        }

    def _trend(self, model_name, keys, months=_TREND_MONTHS):
        """Assemble a per-month time series by calling the service once per
        month. Pure orchestration: the service owns every value; we only place
        them in a list. Date stepping is calendar math, not financial math."""
        report = self._service(model_name)
        today = fields.Date.context_today(self)
        first_of_this_month = today.replace(day=1)
        labels, series = [], {k: [] for k in keys}
        for offset in range(months - 1, -1, -1):
            start = first_of_this_month - relativedelta(months=offset)
            end = start + relativedelta(months=1, days=-1)
            figures = report._nc_service_figures_for(start, end)
            labels.append(start.strftime('%b %Y'))
            for k in keys:
                series[k].append(figures.get(k, 0.0))
        return labels, series

    # ---- cross-domain panels (#56) ---------------------------------------

    def _cross_domain(self, spec):
        """Run one P4-T01 aggregation spec; ``None`` when it yields nothing.

        The engine returns ``None`` — never raises — when the model is absent
        from the registry (the plan never installed that app) or Ring 2 denies
        it (installed but unlicensed for this user). Both mean the same thing
        to a dashboard: render the empty state for that panel and leave the
        rest of the page working. This is why the manifest gains no ``sale`` or
        ``crm`` dependency: a hard dep would make the whole module uninstallable
        where those apps are absent, and break the page where they are merely
        unlicensed.

        Note this module still performs NO aggregation itself — the read lives
        in ``ncollection.aggregation.engine`` (P4-T01), which is exactly what
        ``tests/test_boundary.py`` requires.
        """
        return self.env[_AGGREGATION].aggregate(spec)

    def _pipeline_funnel(self):
        """Open opportunities by stage — value and count. ``None`` without CRM.

        ``type = 'opportunity'`` excludes raw leads, and ``active = True``
        excludes archived/lost ones, so the funnel shows what is genuinely in
        play rather than every record ever created.

        Deliberately NOT period-scoped, unlike _top_customers below. A pipeline
        is a current-state view: what is open right now, whenever it was
        created. Filtering it to the reporting window would hide live deals
        opened before the window and misrepresent the funnel as smaller than it
        is.
        """
        result = self._cross_domain({
            'key': 'pipeline',
            'model': 'crm.lead',
            'domain': [('type', '=', 'opportunity'), ('active', '=', True)],
            'groupby': ['stage_id'],
            'aggregates': ['expected_revenue:sum', '__count'],
        })
        # `None` (absent/unlicensed) and `{'rows': []}` (licensed, nothing this
        # period) are DIFFERENT answers and must not collapse to one. Only the
        # first omits the panel; the second renders an empty state. Conflating
        # them would make a licensed CEO with a quiet month look like a Basic
        # tenant with no CRM at all.
        if result is None:
            return None
        stages = []
        # Engine rows are TUPLES, ordered groupby-then-aggregates — see
        # engine.py's `[tuple(self._flatten_cell(cell) for cell in row) ...]`.
        # They are NOT dicts keyed by field name; unpack positionally, exactly
        # as ncollection_core's own dashboard_data.py does.
        for stage_cell, expected_revenue, count in result['rows']:
            # A grouped many2one always flattens to (id, label) — including the
            # null group, which arrives as (False, ''). Test the ID, not the
            # tuple: a non-empty tuple is always truthy.
            stage_id, stage_label = stage_cell
            stages.append({
                'stage_id': stage_id,
                'label': stage_label or self.env._("Unassigned"),
                'value': expected_revenue or 0.0,
                'count': count or 0,
            })
        return stages

    def _top_customers(self, date_from, date_to, limit=_TOP_CUSTOMERS):
        """Highest-billing customers on confirmed orders IN THE REPORTING
        PERIOD. ``None`` without Sales.

        The window is passed IN, not re-derived here, so it is the same object
        the caller puts in ``meta.period`` — equal by construction, not by two
        independent calls to ``context_today()`` agreeing.

        ``state = 'sale'`` counts confirmed business only — quotations are not
        revenue, and including them would flatter the panel. Odoo 19's
        SALE_ORDER_STATE is draft/sent/sale/cancel; the old ``done`` value is
        gone (replaced by the ``locked`` boolean), so matching it would be a
        term that can never be true. Same filter as
        ``ncollection_core/models/dashboard/dashboard_data.py:341``.

        Bounded to the same window as ``meta.period``. An unbounded all-time
        leaderboard sitting next to a period-scoped KPI row is a figure the
        reader will silently mis-attribute to that period. ``date_order`` is a
        TIMESTAMP, so the bound is half-open (``>= from``, ``< to + 1 day``) —
        a ``<= date_to`` would coerce to midnight and drop everything ordered
        during the final day. Same shape as ``dashboard_data.py:232`` and
        ``kpi.py:217``.
        """
        result = self._cross_domain({
            'key': 'top_customers',
            'model': 'sale.order',
            'domain': [('state', '=', 'sale'),
                       ('date_order', '>=', date_from),
                       ('date_order', '<', date_to + relativedelta(days=1))],
            'groupby': ['partner_id'],
            'aggregates': ['amount_total:sum'],
            'order': 'amount_total:sum desc',
            'limit': limit,
        })
        if result is None:          # absent/unlicensed only — see the funnel
            return None
        customers = []
        # Positional, same as the funnel above — engine rows are tuples.
        for partner_cell, amount_total in result['rows']:
            partner_id, partner_label = partner_cell
            customers.append({
                'partner_id': partner_id,
                'label': partner_label or self.env._("Unknown"),
                'value': amount_total or 0.0,
            })
        return customers

    # ---- public payloads (the OWL client actions call these) -------------

    @api.model
    def get_finance_dashboard(self):
        """Finance dashboard: the eight Financial Summary KPIs + a revenue vs
        expenses trend."""
        current, previous = self._comparison(_SUMMARY)
        kpis = [
            self._kpi('revenue', self.env._("Revenue"), current, previous),
            self._kpi('expenses', self.env._("Expenses"), current, previous),
            self._kpi('net_profit', self.env._("Net Profit"), current, previous),
            self._kpi('cash', self.env._("Cash"), current, previous),
            self._kpi('receivables', self.env._("Receivables"), current, previous),
            self._kpi('payables', self.env._("Payables"), current, previous),
            self._kpi('assets', self.env._("Assets"), current, previous),
            self._kpi('liabilities', self.env._("Liabilities"), current, previous),
        ]
        labels, series = self._trend(_SUMMARY, ('revenue', 'expenses'))
        charts = [{
            'key': 'revenue_expenses',
            'label': self.env._("Revenue vs Expenses"),
            'type': 'line',
            'labels': labels,
            'series': [
                {'name': self.env._("Revenue"), 'data': series['revenue']},
                {'name': self.env._("Expenses"), 'data': series['expenses']},
            ],
        }]
        return {'kpis': kpis, 'charts': charts, 'meta': self._meta()}

    @api.model
    def get_accountant_dashboard(self):
        """Accountant dashboard: profitability KPIs (incl. service-computed
        margins) + a P&L composition bar."""
        current, previous = self._comparison(_PROFITABILITY)
        kpis = [
            self._kpi('total_income', self.env._("Revenue"), current, previous),
            self._kpi('cogs', self.env._("Cost of Sales"), current, previous),
            self._kpi('gross_profit', self.env._("Gross Profit"), current, previous),
            self._kpi('operating_expenses', self.env._("Operating Expenses"), current, previous),
            self._kpi('net_profit', self.env._("Net Profit"), current, previous),
            self._kpi('gross_margin', self.env._("Gross Margin"), current, previous, unit='percent'),
            self._kpi('net_margin', self.env._("Net Margin"), current, previous, unit='percent'),
        ]
        charts = [{
            'key': 'pl_composition',
            'label': self.env._("P&L Composition"),
            'type': 'bar',
            'labels': [self.env._("Revenue"), self.env._("Cost of Sales"),
                       self.env._("Operating Expenses"), self.env._("Net Profit")],
            'series': [{
                'name': self.env._("Amount"),
                'data': [current.get('total_income'), current.get('cogs'),
                         current.get('operating_expenses'), current.get('net_profit')],
            }],
        }]
        return {'kpis': kpis, 'charts': charts, 'meta': self._meta()}

    @api.model
    def get_ceo_dashboard(self):
        """CEO dashboard (#56 / P4-T03): the executive view across domains.

        Headline KPIs and the revenue trend come from the F2-T08 services like
        every other dashboard here. The pipeline funnel and top customers come
        from the P4-T01 engine.

        Three distinct outcomes, deliberately kept distinct:

        * CRM/Sales absent or unlicensed → the panel is **omitted**. Absent
          reads as "not part of your plan", which is the truth. A funnel
          rendered as 0 would read as "no pipeline" — a business claim we have
          no basis to make.
        * Licensed but nothing matched → the panel is **present with empty
          rows**, i.e. a real empty state for a real quiet period.
        * Licensed with data → the panel renders.

        Keeps the {kpis, charts, meta} contract additive: `panels` is a new
        optional key the existing three dashboards simply never populate.
        """
        current, previous = self._comparison(_SUMMARY)
        profit_now, profit_prev = self._comparison(_PROFITABILITY)
        kpis = [
            self._kpi('revenue', self.env._("Revenue"), current, previous),
            self._kpi('net_profit', self.env._("Net Profit"), current, previous),
            self._kpi('cash', self.env._("Cash"), current, previous),
            self._kpi('net_margin', self.env._("Net Margin"),
                      profit_now, profit_prev, unit='percent'),
        ]
        labels, series = self._trend(_SUMMARY, ('revenue', 'net_profit'))
        charts = [{
            'key': 'revenue_vs_profit',
            'label': self.env._("Revenue vs Net Profit"),
            'type': 'line',
            'labels': labels,
            'series': [
                {'name': self.env._("Revenue"), 'data': series['revenue']},
                {'name': self.env._("Net Profit"), 'data': series['net_profit']},
            ],
        }]

        # Resolve the reporting window ONCE and thread it into both the
        # leaderboard's date bound and the meta the client displays. Letting
        # each re-derive it from context_today() would make them equal only by
        # convention — same wall-clock request, same answer — and a request
        # landing on a local-midnight boundary would then bound the leaderboard
        # to a different day than the period shown beside it. That is the exact
        # mis-attribution this panel's date bound exists to prevent, so it is
        # worth making structural rather than incidental.
        summary = self._service(_SUMMARY)
        period_from, period_to = summary.date_from, summary.date_to

        panels = []
        pipeline = self._pipeline_funnel()
        if pipeline is not None:
            panels.append({
                'key': 'pipeline',
                'label': self.env._("Sales Pipeline"),
                'type': 'funnel',
                'rows': pipeline,
                # Drill-down target for the OWL layer (#56 PR 2). Named here so
                # the client never has to know which model backs a panel.
                'drilldown': {'model': 'crm.lead', 'field': 'stage_id'},
            })
        customers = self._top_customers(period_from, period_to)
        if customers is not None:
            panels.append({
                'key': 'top_customers',
                'label': self.env._("Top Customers"),
                'type': 'ranking',
                'rows': customers,
                'drilldown': {'model': 'sale.order', 'field': 'partner_id'},
            })

        return {'kpis': kpis, 'charts': charts, 'panels': panels,
                'meta': self._meta(period_from, period_to)}

    @api.model
    def get_cash_dashboard(self):
        """Cash dashboard: cash / receivables / payables position + a cash
        position trend."""
        current, previous = self._comparison(_SUMMARY)
        kpis = [
            self._kpi('cash', self.env._("Cash"), current, previous),
            self._kpi('receivables', self.env._("Receivables"), current, previous),
            self._kpi('payables', self.env._("Payables"), current, previous),
        ]
        labels, series = self._trend(_SUMMARY, ('cash',))
        charts = [{
            'key': 'cash_position',
            'label': self.env._("Cash Position"),
            'type': 'line',
            'labels': labels,
            'series': [{'name': self.env._("Cash"), 'data': series['cash']}],
        }]
        return {'kpis': kpis, 'charts': charts, 'meta': self._meta()}
