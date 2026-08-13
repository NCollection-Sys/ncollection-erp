# -*- coding: utf-8 -*-
"""F4-T02: budget planning, revision, approval, monitoring.

FPA §7 assigns Budget Planning · Revision · Approval · Monitoring ·
Budget vs Actual to this module. Unlike ``ncollection_account_analytics``, its
spec carries no "Must Never Own: Reports", so the Budget-vs-Actual report lives
here — beside its data — and the dependency runs budget → reports, never the
reverse. An optional module gaining a report is fine; the report engine
requiring a budget module would not be.

NOTHING TO EXTEND. Odoo 19 Community ships no budgeting at all — ``account_budget``
was moved to Enterprise, verified against the running image (680 addons, no
match). So Standing Rule 2's "extend before replacing" has nothing to attach to
here, and this is a genuine build rather than a reinvention.

WHY NOT OCA's ``account_budget_oca``. Its ``crossovered.budget.lines`` carries
``analytic_account_id`` — a SINGLE analytic FK per line. FPA's own report spec
demands filtering by **Department AND Cost Center** at once, and #120 [F4-T01]
established those as independent root ``account.analytic.plan`` records consumed
through ``analytic_distribution``. One FK cannot express two simultaneous
dimensions, so adopting it would put two incompatible analytic mechanisms inside
one report. Budget lines here therefore key off a GL account plus an
``analytic_distribution``, exactly as the actuals do.

THE PERIOD-MISMATCH RULE IS THE ONE JUDGEMENT CALL. A budget covers a span; a
report asks about a different one. FPA does not say what happens when they only
partly overlap. This pro-rates **by day overlap** — a 365-day budget of 36,500
contributes 3,100 to a 31-day month — which is the assumption a reader is most
likely to hold and the one ``mis_builder_budget`` reaches for under its
"accumulation method". It is declared once, as data, in ``_nc_overlap_ratio``
rather than scattered through the arithmetic.
"""
from odoo import api, fields, models
from odoo.exceptions import UserError

STATES = [
    ('draft', 'Draft'),
    ('approved', 'Approved'),
    ('revised', 'Revised'),
]


class NcollectionAccountBudget(models.Model):
    _name = 'ncollection.account.budget'
    _description = 'NCollection Budget'
    # mail.thread IS the audit history the acceptance asks for. Odoo already
    # records who changed a tracked field, to what, and when — a bespoke
    # revision log would be a second, worse copy of that.
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date_from desc, id desc'

    name = fields.Char(required=True, tracking=True)
    company_id = fields.Many2one(
        'res.company', required=True, default=lambda self: self.env.company)
    date_from = fields.Date(required=True, tracking=True)
    date_to = fields.Date(required=True, tracking=True)
    state = fields.Selection(STATES, default='draft', required=True,
                             tracking=True, index=True)
    line_ids = fields.One2many('ncollection.account.budget.line', 'budget_id')
    # A revision points at what it supersedes, so the chain is walkable in both
    # directions and neither copy is silently authoritative.
    revised_from_id = fields.Many2one(
        'ncollection.account.budget', readonly=True, copy=False,
        help="The approved budget this one revises.")
    revision_ids = fields.One2many(
        'ncollection.account.budget', 'revised_from_id', readonly=True)

    @api.constrains('date_from', 'date_to')
    def _check_period(self):
        for budget in self:
            if budget.date_to < budget.date_from:
                raise UserError(self.env._(
                    "A budget cannot end before it starts."))

    # ---- lifecycle -------------------------------------------------------

    def action_approve(self):
        """draft -> approved. Only a draft can be approved."""
        for budget in self:
            if budget.state != 'draft':
                raise UserError(self.env._(
                    "Only a draft budget can be approved; %(name)s is %(state)s.",
                    name=budget.name, state=budget.state))
            if not budget.line_ids:
                raise UserError(self.env._(
                    "%s has no lines, so approving it would approve nothing.",
                    budget.name))
        self.write({'state': 'approved'})

    def action_revise(self):
        """Supersede an approved budget with a draft copy.

        The original is marked ``revised`` and KEPT — a budget that was approved
        and acted on is a record of what was decided, and overwriting it in
        place would destroy the audit history the acceptance asks for. The copy
        starts in draft so it goes through approval on its own merits.
        """
        self.ensure_one()
        if self.state != 'approved':
            raise UserError(self.env._(
                "Only an approved budget can be revised; %(name)s is "
                "%(state)s.", name=self.name, state=self.state))
        revision = self.copy({
            'name': self.env._("%s (revised)", self.name),
            'state': 'draft',
            'revised_from_id': self.id,
        })
        self.write({'state': 'revised'})
        self.message_post(body=self.env._(
            "Revised by %s.", revision.name))
        return revision

    def action_reset_to_draft(self):
        for budget in self:
            if budget.state == 'revised':
                raise UserError(self.env._(
                    "%s has already been superseded by a revision; reopening "
                    "it would leave two live budgets for one period.",
                    budget.name))
        self.write({'state': 'draft'})

    # ---- the figures -----------------------------------------------------

    @api.model
    def _nc_overlap_ratio(self, budget_from, budget_to, date_from, date_to):
        """Share of a budget's span that falls inside a reporting window.

        THE PERIOD-MISMATCH RULE, in one place. Inclusive day counts on both
        sides, so a budget and a window covering the same single day is 1.0 and
        not 0.0 — an off-by-one here silently halves a month.

        Returns 0.0 when the two do not overlap at all.
        """
        start = max(budget_from, date_from)
        end = min(budget_to, date_to)
        if end < start:
            return 0.0
        budget_days = (budget_to - budget_from).days + 1
        if budget_days <= 0:  # pragma: no cover - guarded by _check_period
            return 0.0
        return ((end - start).days + 1) / budget_days

    def _nc_budgeted(self, date_from, date_to, account_types=None):
        """``{account_id: budgeted amount}`` pro-rated into the window.

        Only APPROVED budgets count. A draft is a proposal and a revised one has
        been superseded; including either would report a plan nobody agreed to.
        """
        self.ensure_one() if len(self) == 1 else None
        budgets = self.search([
            ('state', '=', 'approved'),
            ('company_id', '=', self.env.company.id),
            ('date_from', '<=', date_to),
            ('date_to', '>=', date_from),
        ])
        budgeted = {}
        for budget in budgets:
            ratio = self._nc_overlap_ratio(
                budget.date_from, budget.date_to, date_from, date_to)
            if not ratio:
                continue
            for line in budget.line_ids:
                if account_types and line.account_id.account_type not in account_types:
                    continue
                budgeted[line.account_id.id] = (
                    budgeted.get(line.account_id.id, 0.0)
                    + line.amount * ratio)
        return budgeted

    @api.model
    def _nc_variance_payload(self, date_from, date_to):
        """The contract ``ncollection_account_analytics`` (#120) calls.

        #120's ``_nc_budget_variance`` returns ``{'available': False, ...}``
        when this module is absent — deliberately never a zero variance, since
        "no budget" and "exactly on budget" are opposite statements. This is the
        other half: when the module IS here, it answers with the figures.

        ``available`` stays False when no APPROVED budget covers the window,
        for the same reason: an unbudgeted period is not a period on budget.
        """
        budgeted = self.env['ncollection.account.budget']._nc_budgeted(
            date_from, date_to)
        if not budgeted:
            return {
                'available': False,
                'reason': 'No approved budget covers %s to %s.' % (
                    date_from, date_to),
            }
        actual = self.env['ncollection.account.budget.line']._nc_actuals(
            list(budgeted), date_from, date_to)
        budget_total = sum(budgeted.values())
        actual_total = sum(actual.values())
        variance = actual_total - budget_total
        return {
            'available': True,
            'budget': budget_total,
            'actual': actual_total,
            'variance': variance,
            # abs() denominator: a cost budgeted at -100 and spent at -150 is a
            # 50% OVERSPEND; over a signed one it would read +50%, favourable.
            'variance_pct': (variance / abs(budget_total) * 100.0
                             if budget_total else None),
            'date_from': date_from,
            'date_to': date_to,
        }
