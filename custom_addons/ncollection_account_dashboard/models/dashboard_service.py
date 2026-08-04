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

_TREND_MONTHS = 6


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
