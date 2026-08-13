# -*- coding: utf-8 -*-
"""F4-T01: cost centres and profit centres, on Odoo's own analytic plans.

WHY NO DIMENSION MODEL OF OUR OWN. Odoo 19 ships exactly this mechanism, and
``_validate_distribution()`` in ``analytic_mixin`` enforces the 100% rule
**independently per root plan**. So one journal item can carry a Departments
split, a Cost Centers split and a Profit Centers split at the same time, each
validated on its own. Three ``account.analytic.plan`` roots give three
independent dimensions with zero new code and — because ``analytic`` is a DIRECT
dependency of ``account`` — zero new dependency. Inventing an
``ncollection.cost.center`` model would be replacing something Odoo already
does, which Standing Rule 2 forbids, and would strand every OCA and core module
that already understands ``analytic_distribution``.

WHAT A "BREAKDOWN" MEANS HERE. ``analytic_distribution`` is a percentage split,
so a journal item of 1,000 distributed 60/40 contributes 600 and 400 — never
1,000 twice. That is the whole reason this reads
``account.analytic.line.amount`` (which Odoo has already apportioned) rather
than ``account.move.line.balance`` grouped by a distribution key. Summing the
move line would double-count every split item, and the error only appears on
tenants that actually use distributions — the silent kind.

SIGN. ``account.analytic.line.amount`` is credit-positive for revenue and
negative for costs (Odoo posts it as the negated balance), which is already the
orientation a P&L reader expects: income positive, expense negative. This module
does not re-sign it; it reports what Odoo computed (FPA §4/§6).

THIS MODULE MUST NEVER OWN REPORTS (FPA §7). What lives here is the FIGURE
service; rendering it as a report belongs to ``ncollection_account_reports``,
which is #315's job and depends on this ticket.
"""
from odoo import api, models

# The three dimensions FPA names. Each is a ROOT analytic plan, seeded in
# data/analytic_plans.xml; tenants add their own accounts underneath.
PLAN_XMLIDS = {
    'department': 'ncollection_account_analytics.analytic_plan_department',
    'cost_center': 'ncollection_account_analytics.analytic_plan_cost_center',
    'profit_center': 'ncollection_account_analytics.analytic_plan_profit_center',
}


class NcollectionAnalyticsDimension(models.AbstractModel):
    _name = 'ncollection.account.analytics.dimension'
    _description = 'NCollection Analytic Dimension Service'

    @api.model
    def _nc_plan(self, dimension):
        """The root ``account.analytic.plan`` for a dimension key, or empty.

        Empty rather than an exception when the record is missing: a tenant may
        legitimately have deleted a seeded plan it does not use, and a dashboard
        tile that disappears is honest where a 500 is an outage — the governing
        principle of the aggregation engine this service feeds.
        """
        xmlid = PLAN_XMLIDS.get(dimension)
        if not xmlid:
            return self.env['account.analytic.plan'].browse()
        return self.env.ref(xmlid, raise_if_not_found=False) or \
            self.env['account.analytic.plan'].browse()

    @api.model
    def _nc_breakdown(self, dimension, date_from, date_to, company=None):
        """``[{analytic_account_id, name, amount}]`` for one dimension.

        Reads ``account.analytic.line``, which Odoo writes with the amount
        ALREADY apportioned by the distribution percentage — see the module
        docstring for why summing the move line instead would double-count.

        Returns ``None`` when the dimension's plan is absent, propagating "this
        tenant cannot answer that" the way the aggregation engine's contract
        requires, rather than an empty list that reads as "no activity".
        """
        plan = self._nc_plan(dimension)
        if not plan:
            return None
        company = company or self.env.company
        accounts = self.env['account.analytic.account'].search(
            [('plan_id', 'child_of', plan.id)])
        if not accounts:
            return []
        # ONE COLUMN PER PLAN, not a single `account_id`. Odoo's
        # `plan._strict_column_name()` returns `account_id` only for the
        # "project plan" (the first one) and `x_plan<id>_id` — a column it
        # CREATES at runtime — for every other. Grouping by `account_id` finds
        # nothing for any plan but the first, which is a breakdown that renders
        # permanently empty rather than raising. Asking the plan for its own
        # column name is the only correct way, and it keeps working if a tenant
        # deletes plans and one of ours becomes the project plan.
        column = plan._column_name()
        rows = self.env['account.analytic.line']._read_group(
            [(column, 'in', accounts.ids),
             ('date', '>=', date_from), ('date', '<=', date_to),
             ('company_id', '=', company.id)],
            groupby=[column], aggregates=['amount:sum'])
        return sorted(
            [{'analytic_account_id': account.id,
              'name': account.display_name,
              'amount': amount or 0.0}
             for account, amount in rows if account],
            key=lambda row: row['name'])

    @api.model
    def _nc_total(self, dimension, date_from, date_to, company=None):
        """The dimension's total, or ``None`` when it cannot be computed.

        Deliberately derived from ``_nc_breakdown`` rather than its own
        aggregate: two queries that are supposed to agree eventually disagree,
        and the one a reader checks is the breakdown.
        """
        rows = self._nc_breakdown(dimension, date_from, date_to, company=company)
        if rows is None:
            return None
        return sum(row['amount'] for row in rows)
