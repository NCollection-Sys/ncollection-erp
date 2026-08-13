# -*- coding: utf-8 -*-
"""SLA policies for helpdesk tickets (P6-T03 / #67).

A policy answers one question: given a ticket's team and priority, how long
may it take to be *responded to*, and how long to be *resolved*? Everything
else in this module is bookkeeping on top of that answer.

WHY A MODEL RATHER THAN CONFIG PARAMETERS. Two `ir.config_parameter` keys per
priority would have been less code, but SLAs are per-team in every real
helpdesk (a Finance queue and an Infrastructure queue do not promise the same
hours), and a config key cannot express "this team, this priority". The model
also makes the promise auditable — who changed the commitment, and when — via
the standard chatter on a record, which a config parameter has no room for.

Deliberately NOT a working-calendar SLA. Odoo core ships `resource.calendar`
and a real "8 business hours" clock would use it, but that pulls the whole
resource/leave surface in for a first cut and makes every test depend on a
calendar fixture. Elapsed hours is the honest v1: it is what the field name
says, and the docstring here is the only place that could mislead a reader
into thinking otherwise. Business-hours SLAs are a follow-up, not a silent gap.
"""

from odoo import api, fields, models
from odoo.exceptions import ValidationError

#: Mirrors helpdesk.ticket.priority, read off the installed model rather than
#: guessed: OCA ships 0..3. Kept as a module constant so the policy selection
#: and the matcher cannot drift apart.
PRIORITIES = [
    ('0', 'Low'),
    ('1', 'Normal'),
    ('2', 'High'),
    ('3', 'Urgent'),
]


class NcHelpdeskSlaPolicy(models.Model):
    _name = 'ncollection.helpdesk.sla.policy'
    _description = 'NCollection Helpdesk SLA Policy'
    _inherit = ['mail.thread']
    _order = 'sequence, id'

    name = fields.Char(required=True, tracking=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        'res.company', required=True, index=True,
        default=lambda self: self.env.company)

    # An empty team is the FALLBACK, not "all teams". _match() prefers a
    # team-specific policy and only falls back here, so a general policy can
    # sit alongside a stricter one without the two fighting.
    team_id = fields.Many2one(
        'helpdesk.ticket.team', tracking=True,
        help="Leave empty for the fallback policy used by any team that has "
             "no policy of its own.")
    priority = fields.Selection(
        PRIORITIES, required=True, default='1', tracking=True)

    response_hours = fields.Float(
        string='Response (hours)', required=True, default=8.0, tracking=True,
        help="Hours from ticket creation within which someone must respond.")
    resolution_hours = fields.Float(
        string='Resolution (hours)', required=True, default=48.0, tracking=True,
        help="Hours from ticket creation within which the ticket must reach a "
             "closing stage.")

    # One policy per (company, team, priority). Without this a second policy
    # for the same combination makes _match()'s answer depend on row order,
    # i.e. on nothing a user can see or control.
    #
    # Declared via models.Constraint, NOT the legacy `_sql_constraints` list:
    # Odoo 19 silently ignores that list, so the index is simply never created
    # and the guard reads as present while enforcing nothing. This repo has
    # already been bitten by exactly that (see the same note in
    # ncollection_billing/models/account_move.py) — and it bit this module too,
    # caught only because the duplicate test asserted the raise instead of
    # trusting the declaration.
    _nc_sla_unique_combo = models.Constraint(
        'unique(company_id, team_id, priority)',
        'There is already an SLA policy for this company, team and priority.')

    @api.constrains('response_hours', 'resolution_hours')
    def _check_hours(self):
        for policy in self:
            if policy.response_hours <= 0 or policy.resolution_hours <= 0:
                raise ValidationError(self.env._(
                    "SLA hours must be greater than zero (policy \"%s\").",
                    policy.name))
            # A resolution promise shorter than the response promise is not a
            # stricter SLA, it is an unsatisfiable one: the ticket would breach
            # resolution before anyone was even required to look at it.
            if policy.resolution_hours < policy.response_hours:
                raise ValidationError(self.env._(
                    "Resolution hours cannot be shorter than response hours "
                    "(policy \"%s\").", policy.name))

    #: Editing any of these changes what a ticket's deadlines SHOULD be.
    #: `hours` change the answer for tickets already matched here; the rest
    #: change WHICH tickets match at all, so they need the wider sweep.
    _DEADLINE_FIELDS = frozenset({'response_hours', 'resolution_hours'})
    _MATCHING_FIELDS = frozenset({'team_id', 'priority', 'active', 'company_id'})

    def write(self, vals):
        """Push a changed commitment onto the tickets it governs.

        WHY THIS OVERRIDE EXISTS. `helpdesk.ticket._compute_nc_sla_deadlines`
        depends on the TICKET's team/priority/company/create_date — it cannot
        depend on the policy, because the policy is found by a search rather
        than reached through a field, and `@api.depends` cannot express that.
        Without this, editing a policy's hours left every already-matched
        ticket on its old deadline forever: no write to the ticket, and the
        cron only refreshes `nc_sla_state`, which is derived from those stale
        deadlines. The badge would then be confidently wrong rather than
        merely late — the silent-wrong-while-green failure this repo's guards
        exist to prevent.

        Scope of the sweep is deliberately asymmetric: an hours edit only
        affects tickets already matched to this policy, while a team/priority/
        active change alters the MATCH itself, so open tickets in the affected
        companies are re-evaluated too. Closed tickets are left alone — their
        verdict is already recorded and must not be rewritten by a later
        change of policy.
        """
        touches_deadline = self._DEADLINE_FIELDS & set(vals)
        touches_matching = self._MATCHING_FIELDS & set(vals)
        companies = self.mapped('company_id')
        res = super().write(vals)
        if not (touches_deadline or touches_matching):
            return res

        Ticket = self.env['helpdesk.ticket'].sudo()
        affected = Ticket.search([('nc_sla_policy_id', 'in', self.ids)])
        if touches_matching:
            companies |= self.mapped('company_id')
            affected |= Ticket.search([
                ('closed', '=', False),
                ('company_id', 'in', companies.ids)])
        if affected:
            affected._compute_nc_sla_deadlines()
            affected._nc_sla_refresh()
        return res

    @api.model
    def _match(self, team, priority, company):
        """The policy that governs a ticket, or an empty recordset.

        Team-specific beats fallback; nothing else is consulted. Returns at
        most one record — the SQL constraint above guarantees the (company,
        team, priority) key is unique, so `limit=1` is a real answer rather
        than an arbitrary pick among equals.
        """
        base = [('company_id', '=', company.id), ('priority', '=', priority)]
        if team:
            specific = self.search(base + [('team_id', '=', team.id)], limit=1)
            if specific:
                return specific
        return self.search(base + [('team_id', '=', False)], limit=1)
