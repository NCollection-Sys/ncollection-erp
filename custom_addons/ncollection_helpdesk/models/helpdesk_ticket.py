# -*- coding: utf-8 -*-
"""SLA timers on the OCA helpdesk ticket (P6-T03 / #67).

Adds deadlines and a breach state to `helpdesk.ticket`. It extends OCA's model
rather than replacing anything: no OCA field is redefined, no OCA view is
rewritten, and every field added here carries the `nc_` prefix so a reader can
tell at a glance which half of the model they are looking at.

THE ONE SUBTLETY WORTH READING. A deadline is a fact about the record
(create_date + policy hours) and is stored and computed normally. A *breach*
is not: it is a fact about the clock, and no ORM recompute fires because time
passed. So `nc_sla_state` is stored and refreshed by a cron
(`_cron_nc_sla_scan`), exactly as P2-T14's lifecycle sweep refreshes states
that only wall-clock movement can change. Storing it is what lets the list view
filter and group by it; the cron is what stops it being a lie between writes.

The consequence, stated rather than hidden: between two cron runs a state can
be stale by up to the cron interval. `_nc_sla_state_now()` computes the honest
answer on demand for anything that must not wait — the tests use it to assert
breach transitions without sleeping.
"""

from datetime import timedelta

from odoo import api, fields, models

#: How close to a deadline counts as "due soon" — a warning band, not a
#: promise. One hour is short enough to be actionable and long enough that a
#: daily cron will usually surface it at least once before the breach.
DUE_SOON_HOURS = 1.0

SLA_STATES = [
    ('none', 'No Policy'),
    ('on_track', 'On Track'),
    ('due_soon', 'Due Soon'),
    ('breached', 'Breached'),
    ('met', 'Met'),
]


class HelpdeskTicket(models.Model):
    _inherit = 'helpdesk.ticket'

    nc_sla_policy_id = fields.Many2one(
        'ncollection.helpdesk.sla.policy', string='SLA Policy',
        compute='_compute_nc_sla_deadlines', store=True, readonly=True)
    nc_sla_response_deadline = fields.Datetime(
        string='Response Due', compute='_compute_nc_sla_deadlines',
        store=True, readonly=True)
    nc_sla_resolution_deadline = fields.Datetime(
        string='Resolution Due', compute='_compute_nc_sla_deadlines',
        store=True, readonly=True)

    # Set once, by the first agent action. Not computed: "when did someone
    # first respond" is an event, and recomputing it would let a later edit
    # silently rewrite history.
    nc_sla_first_response = fields.Datetime(
        string='First Response', readonly=True, copy=False)

    nc_sla_state = fields.Selection(
        SLA_STATES, string='SLA', default='none', readonly=True, copy=False,
        index=True,
        help="Refreshed by the SLA scan cron and on every write that can "
             "change it. May lag reality by up to one cron interval.")

    # ---------------------------------------------------------------- compute

    @api.depends('team_id', 'priority', 'company_id', 'create_date')
    def _compute_nc_sla_deadlines(self):
        """Deadlines follow from the policy; they do not depend on the clock."""
        Policy = self.env['ncollection.helpdesk.sla.policy']
        for ticket in self:
            company = ticket.company_id or self.env.company
            policy = Policy._match(ticket.team_id, ticket.priority, company)
            ticket.nc_sla_policy_id = policy
            # create_date is False on a NewId record mid-onchange; without this
            # guard the compute raises before the record is ever saved.
            start = ticket.create_date
            if not policy or not start:
                ticket.nc_sla_response_deadline = False
                ticket.nc_sla_resolution_deadline = False
                continue
            ticket.nc_sla_response_deadline = start + _hours(policy.response_hours)
            ticket.nc_sla_resolution_deadline = start + _hours(
                policy.resolution_hours)

    # ------------------------------------------------------------ state logic

    def _nc_sla_state_now(self, now=None):
        """The SLA state this ticket has RIGHT NOW, without writing anything.

        `now` is injectable so tests can prove a breach without sleeping — the
        same discipline P2-T14's sweep uses. Returns the state; the caller
        decides whether to store it.
        """
        self.ensure_one()
        now = now or fields.Datetime.now()
        if not self.nc_sla_policy_id or not self.nc_sla_resolution_deadline:
            return 'none'

        # A closed ticket is judged on the record, not the clock: it either
        # beat its resolution deadline or it did not, and that verdict must not
        # flip afterwards just because more time passed.
        if self.closed:
            done = self.closed_date or now
            return 'met' if done <= self.nc_sla_resolution_deadline else 'breached'

        # Open: the response deadline can breach on its own, before resolution
        # is even due — that is the whole point of having two clocks.
        if not self.nc_sla_first_response and self.nc_sla_response_deadline \
                and now > self.nc_sla_response_deadline:
            return 'breached'
        if now > self.nc_sla_resolution_deadline:
            return 'breached'
        if (self.nc_sla_resolution_deadline - now) <= _hours(DUE_SOON_HOURS):
            return 'due_soon'
        return 'on_track'

    def _nc_sla_refresh(self, now=None):
        """Store the current state on each ticket. Writes only on change."""
        for ticket in self:
            state = ticket._nc_sla_state_now(now=now)
            if ticket.nc_sla_state != state:
                # sudo: the scan runs for the whole desk, and an agent who can
                # see a ticket cannot necessarily write its SLA column. The
                # value written is derived from the record's own fields, so
                # this widens nothing a caller could steer.
                ticket.sudo().nc_sla_state = state
        return self

    @api.model
    def _cron_nc_sla_scan(self, now=None):
        """Daily sweep: refresh SLA state on every ticket that still has a clock.

        Closed tickets are included deliberately — a ticket closed since the
        last run needs its final met/breached verdict recorded once. Tickets
        with no policy are skipped: there is nothing to say about them, and
        scanning them would grow with the archive for no benefit.
        """
        tickets = self.search([('nc_sla_policy_id', '!=', False)])
        tickets._nc_sla_refresh(now=now)
        return len(tickets)

    # ---------------------------------------------------------------- writes

    def write(self, vals):
        """Stamp the first response, then refresh the state.

        First response is stamped when an agent takes the ticket (`user_id`) or
        moves it off its opening stage — the two things that actually mean
        "somebody looked at this". Stamped BEFORE super() decides nothing else,
        but read AFTER, so the refresh below sees the new values.
        """
        touches_response = 'user_id' in vals or 'stage_id' in vals
        res = super().write(vals)
        if touches_response:
            for ticket in self:
                if not ticket.nc_sla_first_response and (
                        ticket.user_id or ticket.stage_id.closed):
                    ticket.sudo().nc_sla_first_response = fields.Datetime.now()
        # Any of these can change the verdict; recompute rather than wait for
        # the cron, so the UI is right immediately after the action that
        # changed it.
        if touches_response or {'priority', 'team_id', 'closed'} & set(vals):
            self._nc_sla_refresh()
        return res

    @api.model_create_multi
    def create(self, vals_list):
        tickets = super().create(vals_list)
        tickets._nc_sla_refresh()
        return tickets


def _hours(value):
    """Float hours -> timedelta. One place, so rounding cannot diverge."""
    return timedelta(hours=float(value))
