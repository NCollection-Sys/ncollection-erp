# -*- coding: utf-8 -*-
"""F4-T01: budget variance and trend extrapolation.

VARIANCE IS A SOFT DEPENDENCY, AND SOFT MEANS IT SAYS SO. The ticket's own
acceptance reads "variance analysis vs budget **when F4-T02 present**".
``ncollection_account_budget`` (#121) is still open, so on every tenant today
there is no budget to vary against. The honest answer is a result that says
"no budget module" — not 0.00, which reads as "you are exactly on budget" and is
the most dangerous number this file could return.

The presence check is registry membership (``self.env.get(...)``), NOT
``_nc_feature_enabled``. That helper answers "does the tenant's PLAN license
this app", its docstring calls passing an ``ncollection*`` module "a contract
violation", and it fail-opens to True — so using it here would report a budget
module that does not exist as available.

"BASIC FORECASTING" HAS NO SPECIFICATION ANYWHERE, so this implements the
smallest defensible thing and labels it honestly: an ordinary-least-squares
trend through N closed periods, extrapolated forward. It is called a TREND, not
a prediction, and it carries the sample size and the fit's own slope so a reader
can judge it. What it deliberately is not: no seasonality, no confidence
interval, no smoothing constant. Each of those is a modelling choice that needs
a finance owner's sign-off, and a forecast that hides its method behind a
tuned constant is a number nobody can argue with — which is the opposite of
useful.

A trend needs history. Fewer than ``_MIN_PERIODS`` closed periods returns
``None`` rather than fitting a line through two points and calling it a
forecast.
"""
from odoo import api, models

# Below this, a "trend" is just noise with a slope.
_MIN_PERIODS = 3


class NcollectionAnalyticsForecast(models.AbstractModel):
    _name = 'ncollection.account.analytics.forecast'
    _description = 'NCollection Financial Variance and Trend Service'

    # ---- variance ---------------------------------------------------------

    @api.model
    def _nc_budget_model(self):
        """The budget model, or ``None`` when #121 is not installed here.

        Registry membership, not a licence check — see the module docstring for
        why ``_nc_feature_enabled`` is the wrong instrument.
        """
        return self.env.get('ncollection.account.budget')

    @api.model
    def _nc_variance(self, actual, budget):
        """``(variance, variance_pct)`` of actual against budget.

        Percentage over ``abs(budget)`` so the SIGN always reports the
        real-world direction: over a signed denominator a cost budgeted at -100
        and spent at -150 would read as +50% — the opposite of the overspend it
        is. The report engine's comparison mixin negates the identical trap, and
        this file matching it is deliberate.
        """
        variance = actual - budget
        return variance, (variance / abs(budget) * 100.0) if budget else None

    @api.model
    def _nc_budget_variance(self, date_from, date_to):
        """Actual-vs-budget, or a result that states why it cannot be computed.

        Returns ``{'available': False, 'reason': ...}`` when #121 is absent.
        Never a zero variance — "no budget to compare against" and "exactly on
        budget" are opposite statements about the business.
        """
        budget_model = self._nc_budget_model()
        if budget_model is None:
            return {
                'available': False,
                'reason': 'ncollection_account_budget (F4-T02, #121) is not '
                          'installed on this tenant, so there is no budget to '
                          'compare against.',
            }
        # #121 owns the budget figure; this module owns the arithmetic. The
        # contract is deliberately narrow so F4-T02 can ship its model without
        # this file changing.
        return budget_model._nc_variance_payload(date_from, date_to)

    # ---- trend ------------------------------------------------------------

    @api.model
    def _nc_trend(self, series):
        """Least-squares slope/intercept over ``series``, or ``None``.

        ``series`` is an ordered list of numbers, one per closed period, oldest
        first. Returns ``{'slope', 'intercept', 'periods'}`` where the fitted
        line is ``intercept + slope * index``.

        ``None`` under ``_MIN_PERIODS`` samples, and ``None`` when every x is
        identical (a degenerate fit) — both cases would otherwise produce a
        confident-looking line with no information in it.
        """
        values = [v for v in (series or []) if v is not None]
        if len(values) < _MIN_PERIODS:
            return None
        n = len(values)
        xs = list(range(n))
        mean_x = sum(xs) / n
        mean_y = sum(values) / n
        denominator = sum((x - mean_x) ** 2 for x in xs)
        if not denominator:
            return None
        slope = sum((x - mean_x) * (y - mean_y)
                    for x, y in zip(xs, values)) / denominator
        return {'slope': slope,
                'intercept': mean_y - slope * mean_x,
                'periods': n}

    @api.model
    def _nc_extrapolate(self, series, ahead=1):
        """The trend's value ``ahead`` periods past the end of ``series``.

        Returns ``{'value', 'slope', 'periods', 'method'}`` or ``None``. The
        ``method`` string travels with the number on purpose: a forecast whose
        derivation is not attached to it is a number a reader will assume more
        of than it deserves.
        """
        trend = self._nc_trend(series)
        if trend is None or ahead < 1:
            return None
        index = trend['periods'] - 1 + ahead
        return {
            'value': trend['intercept'] + trend['slope'] * index,
            'slope': trend['slope'],
            'periods': trend['periods'],
            'method': 'ordinary least squares over %d closed periods; no '
                      'seasonality, no confidence interval' % trend['periods'],
        }
