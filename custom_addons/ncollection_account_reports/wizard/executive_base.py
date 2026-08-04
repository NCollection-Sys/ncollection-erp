# -*- coding: utf-8 -*-
"""F2-T08: the executive-report composition layer.

The FPA §10 Executive Reports (Financial Summary, Revenue Analysis, Expense
Analysis, Profitability) are **views over figures F2-T03 already computes** —
they are not a second accounting implementation.

That is the ticket's third acceptance criterion ("F3 dashboards consume these
services rather than recomputing") turned into architecture, one level lower:
every number here comes from the Balance Sheet / Profit & Loss wizards or the
F2-T01 engine, reached by spawning an in-memory sibling wizard carrying this
report's own filters. Nothing in this layer touches ``account.move.line``, and
``tests/test_executive_reports.py`` FAILS if that ever changes — so a future
edit cannot quietly reintroduce a parallel arithmetic that drifts from the
statements it is supposed to summarise.

Composition, not modification: no F2-T03 file is edited to support this.
"""
from odoo import models

# The filters an executive report hands down to the statement wizard it composes.
# Kept explicit rather than "copy every field": the child must inherit the user's
# SCOPE (company, period, journals, accounts, partners, posted-vs-all) and
# nothing else — a stray comparison_type would make the child compute a second,
# unwanted comparison period.
_INHERITED_FILTERS = (
    'company_id', 'date_from', 'date_to', 'target_move',
    'journal_ids', 'account_ids', 'partner_ids',
)


class NcollectionExecutiveReport(models.AbstractModel):
    _name = 'ncollection.account.report.executive'
    _description = 'NCollection Executive Report Composition'

    # ---- composing the underlying statements -----------------------------

    def _nc_child_values(self):
        """This report's filters, shaped for a statement wizard."""
        self.ensure_one()
        values = {}
        for name in _INHERITED_FILTERS:
            field = self._fields[name]
            value = self[name]
            values[name] = [(6, 0, value.ids)] if field.type == 'many2many' else (
                value.id if field.type == 'many2one' else value)
        # The child renders nothing; it only computes. Its own comparison is
        # driven explicitly by _nc_statement(), never inherited from here.
        values['comparison_type'] = 'none'
        return values

    def _nc_statement(self, model_name, date_from=None, date_to=None):
        """An in-memory statement wizard scoped to this report's filters.

        ``new()`` (not ``create()``) — these are pure computation records that
        must never hit the database or the transient-vacuum. Pass dates to
        evaluate a different period; the comparison mixin's own
        ``_nc_comparison_range()`` supplies them.
        """
        self.ensure_one()
        values = self._nc_child_values()
        if date_from is not None:
            values['date_from'] = date_from
        if date_to is not None:
            values['date_to'] = date_to
        return self.env[model_name].new(values)

    def _nc_pl_full(self, date_from=None, date_to=None):
        """``(figures, details)`` from the Profit & Loss — F2-T03 owns the math.

        ``details`` is ``{bucket: [(account, signed balance)]}``, which the
        Revenue/Expense Analysis reports render as per-account rows.
        """
        statement = self._nc_statement(
            'ncollection.account.report.profit.loss', date_from, date_to)
        return statement._nc_pl_figures(statement)

    def _nc_pl(self, date_from=None, date_to=None):
        """Just the Profit & Loss figures dict for a period."""
        return self._nc_pl_full(date_from, date_to)[0]

    def _nc_bs_full(self, date_to=None):
        """``(bucket totals, {account_type: signed balance})`` as of a date.

        ONE Balance Sheet evaluation serves both. Financial Summary needs bucket
        totals (Assets, Liabilities) *and* individual ``account_type`` figures
        (Cash, Receivables, Payables) — the latter live inside the buckets
        rather than being buckets themselves. ``_nc_bucket_totals`` already
        returns a per-account signed map as its third element, so the by-type
        view is folded from that instead of costing a second aggregate over the
        same company/date scope.
        """
        statement = self._nc_statement(
            'ncollection.account.report.balance.sheet', date_to=date_to)
        totals, _details, raw = statement._nc_bucket_totals(statement)
        accounts = self.env['account.account'].with_context(
            active_test=False).browse(list(raw))
        by_type = {}
        for account in accounts:
            by_type[account.account_type] = (
                by_type.get(account.account_type, 0.0) + raw[account.id])
        return totals, by_type

    # ---- the service surface F3 / #56 will consume -----------------------

    def _nc_service_figures(self):
        """``{key: current value}`` — this report's headline figures.

        THE consumption point for F3 dashboards (#56 [P4-T03]): a dashboard
        calls this instead of re-deriving anything. Concrete reports implement
        it; ``_nc_compute_lines`` renders from the same dict, so the rendered
        report and the dashboard can never disagree.
        """
        raise NotImplementedError

    def _nc_service_comparison(self):
        """``(current, previous)`` figure dicts — the same service, over both
        periods. ``previous`` is empty when no comparison is requested."""
        self.ensure_one()
        current = self._nc_service_figures()
        window = self._nc_comparison_range()
        if window is None:
            return current, {}
        return current, self._nc_service_figures_for(*window)

    def _nc_service_figures_for(self, date_from, date_to):
        """``_nc_service_figures`` evaluated over an explicit period."""
        raise NotImplementedError

    # ---- shared rendering -------------------------------------------------

    def _nc_metric_rows(self, layout):
        """Render ``[(label, key, level)]`` into comparison rows.

        Every executive report is a flat list of named metrics, so the row
        building is identical across all four — only the layout differs.
        """
        self.ensure_one()
        current, previous = self._nc_service_comparison()
        rows = []
        for label, key, level in layout:
            if key is None:                       # section header, no figures
                rows.append(self._nc_comparison_row(label, 0.0, 0.0, level=level))
                continue
            rows.append(self._nc_comparison_row(
                label, current.get(key, 0.0), previous.get(key, 0.0), level=level))
        return rows

    @staticmethod
    def _nc_ratio(numerator, denominator):
        """A percentage, or 0.0 when the denominator is zero.

        Same contract as the comparison mixin's ``variance_pct``: a financial
        report must never render ``inf`` or raise in a cell. Uses ``abs()`` on
        the denominator so the sign reports the real direction.
        """
        return (numerator / abs(denominator) * 100.0) if denominator else 0.0
