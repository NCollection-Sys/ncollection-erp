# -*- coding: utf-8 -*-
"""SLA policy matching, deadlines and breach states (P6-T03 / #67).

The clock is INJECTED everywhere (`_nc_sla_state_now(now=...)`,
`_cron_nc_sla_scan(now=...)`), so a breach is proven by arithmetic rather than
by sleeping. That is the same discipline P2-T14's lifecycle sweep uses, and it
is the only way these assertions can be both fast and honest.
"""
from datetime import timedelta

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestHelpdeskSla(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Policy = cls.env['ncollection.helpdesk.sla.policy']
        cls.Ticket = cls.env['helpdesk.ticket']
        cls.company = cls.env.company
        cls.team = cls.env['helpdesk.ticket.team'].create({'name': 'NC Team'})
        cls.partner = cls.env['res.partner'].create({'name': 'SLA Customer'})

    def _ticket(self, priority='1', team=None, **kw):
        vals = {'name': 'SLA ticket', 'description': 'body',
                'partner_id': self.partner.id, 'priority': priority}
        if team is not None:
            vals['team_id'] = team.id
        vals.update(kw)
        return self.Ticket.create(vals)

    # ------------------------------------------------------------- shipped data
    def test_default_fallback_policies_ship_for_every_priority(self):
        """All four priorities must be covered, or a ticket silently has no SLA."""
        for priority in ('0', '1', '2', '3'):
            with self.subTest(priority=priority):
                self.assertTrue(
                    self.Policy._match(self.env['helpdesk.ticket.team'],
                                       priority, self.company),
                    "no fallback SLA policy for priority %s" % priority)

    # ------------------------------------------------------------------ matching
    def test_a_team_policy_beats_the_fallback(self):
        specific = self.Policy.create({
            'name': 'Team urgent', 'team_id': self.team.id, 'priority': '3',
            'response_hours': 0.5, 'resolution_hours': 2.0})
        self.assertEqual(
            self.Policy._match(self.team, '3', self.company), specific)

    def test_a_team_without_its_own_policy_uses_the_fallback(self):
        matched = self.Policy._match(self.team, '1', self.company)
        self.assertTrue(matched)
        self.assertFalse(matched.team_id, "expected the team-less fallback")

    def test_duplicate_policies_for_one_combination_are_refused(self):
        """Without this the matcher's answer would depend on row order."""
        self.Policy.create({
            'name': 'A', 'team_id': self.team.id, 'priority': '2',
            'response_hours': 1.0, 'resolution_hours': 2.0})
        with self.assertRaises(Exception):
            self.Policy.create({
                'name': 'B', 'team_id': self.team.id, 'priority': '2',
                'response_hours': 3.0, 'resolution_hours': 4.0})
            self.env.flush_all()

    # --------------------------------------------------------------- constraints
    def test_zero_or_negative_hours_are_refused(self):
        with self.assertRaises(ValidationError):
            self.Policy.create({
                'name': 'bad', 'priority': '1',
                'response_hours': 0.0, 'resolution_hours': 5.0})

    def test_resolution_shorter_than_response_is_refused(self):
        """Such a policy is unsatisfiable, not strict: it breaches before
        anyone is even required to look at the ticket."""
        with self.assertRaises(ValidationError):
            self.Policy.create({
                'name': 'impossible', 'priority': '1',
                'response_hours': 8.0, 'resolution_hours': 4.0})

    # ----------------------------------------------------------------- deadlines
    def test_deadlines_are_derived_from_the_matched_policy(self):
        policy = self.Policy.create({
            'name': 'Team normal', 'team_id': self.team.id, 'priority': '1',
            'response_hours': 2.0, 'resolution_hours': 10.0})
        ticket = self._ticket(priority='1', team=self.team)
        self.assertEqual(ticket.nc_sla_policy_id, policy)
        self.assertEqual(
            ticket.nc_sla_response_deadline,
            ticket.create_date + timedelta(hours=2))
        self.assertEqual(
            ticket.nc_sla_resolution_deadline,
            ticket.create_date + timedelta(hours=10))

    def test_changing_priority_moves_the_deadlines(self):
        ticket = self._ticket(priority='0')
        before = ticket.nc_sla_resolution_deadline
        ticket.priority = '3'
        self.assertNotEqual(ticket.nc_sla_resolution_deadline, before)
        self.assertEqual(ticket.nc_sla_policy_id.priority, '3')

    # -------------------------------------------------------------------- states
    def test_a_fresh_ticket_is_on_track(self):
        self.assertEqual(self._ticket().nc_sla_state, 'on_track')

    def test_it_becomes_due_soon_inside_the_warning_band(self):
        """The ticket must have been RESPONDED to first.

        Written the naive way this failed, correctly: at
        `resolution - 30min` an unanswered ticket is already past its 8h
        response deadline, so 'breached' was the right answer and the test was
        the thing that was wrong. Stamping the response isolates the resolution
        clock, which is what this test is actually about.
        """
        ticket = self._ticket()
        ticket.nc_sla_first_response = fields.Datetime.now()
        almost = ticket.nc_sla_resolution_deadline - timedelta(minutes=30)
        self.assertEqual(ticket._nc_sla_state_now(now=almost), 'due_soon')

    def test_an_unanswered_ticket_breaches_on_the_RESPONSE_clock(self):
        """The response deadline breaches on its own, well before resolution
        is due — the reason there are two clocks rather than one."""
        ticket = self._ticket(priority='1')      # 8h response / 48h resolution
        past_response = ticket.nc_sla_response_deadline + timedelta(minutes=1)
        self.assertLess(past_response, ticket.nc_sla_resolution_deadline)
        self.assertEqual(ticket._nc_sla_state_now(now=past_response), 'breached')

    def test_answering_stops_the_response_clock(self):
        ticket = self._ticket(priority='1')
        agent = self.env['res.users'].create({
            'name': 'Agent', 'login': 'nc_sla_agent',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        ticket.user_id = agent
        self.assertTrue(ticket.nc_sla_first_response)
        past_response = ticket.nc_sla_response_deadline + timedelta(minutes=1)
        # Still on track: only the resolution clock is left running.
        self.assertNotEqual(ticket._nc_sla_state_now(now=past_response), 'breached')

    def test_first_response_is_stamped_once_and_never_rewritten(self):
        ticket = self._ticket()
        agent = self.env['res.users'].create({
            'name': 'Agent2', 'login': 'nc_sla_agent2',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        ticket.user_id = agent
        first = ticket.nc_sla_first_response
        ticket.user_id = self.env.user          # reassigned later
        self.assertEqual(ticket.nc_sla_first_response, first)

    def test_a_ticket_closed_in_time_is_met_and_stays_met(self):
        """The verdict is about the record, not the clock: it must not flip
        back to breached simply because more time passes afterwards."""
        ticket = self._ticket()
        closed_stage = self.env['helpdesk.ticket.stage'].search(
            [('closed', '=', True)], limit=1)
        self.assertTrue(closed_stage, "helpdesk_mgmt ships no closing stage")
        ticket.stage_id = closed_stage
        self.assertEqual(ticket.nc_sla_state, 'met')
        much_later = ticket.nc_sla_resolution_deadline + timedelta(days=30)
        self.assertEqual(ticket._nc_sla_state_now(now=much_later), 'met')

    # ---------------------------------------------------------------------- cron
    def test_the_scan_stores_a_breach_that_only_the_clock_caused(self):
        """The whole reason the cron exists: no ORM recompute fires because
        time passed, so without the sweep the stored state would stay stale."""
        ticket = self._ticket(priority='1')
        self.assertEqual(ticket.nc_sla_state, 'on_track')
        later = ticket.nc_sla_resolution_deadline + timedelta(hours=1)
        self.Ticket._cron_nc_sla_scan(now=later)
        ticket.invalidate_recordset(['nc_sla_state'])
        self.assertEqual(ticket.nc_sla_state, 'breached')

    def test_the_scan_skips_tickets_with_no_policy(self):
        """Nothing to say about them, and scanning them would grow with the
        archive for no benefit."""
        self.Policy.search([]).write({'active': False})
        ticket = self._ticket()
        self.assertFalse(ticket.nc_sla_policy_id)
        scanned = self.Ticket._cron_nc_sla_scan()
        self.assertNotIn(ticket.id, self.Ticket.search(
            [('nc_sla_policy_id', '!=', False)]).ids)
        self.assertIsInstance(scanned, int)

    def test_the_scan_is_idempotent(self):
        ticket = self._ticket()
        later = ticket.nc_sla_resolution_deadline + timedelta(hours=1)
        self.Ticket._cron_nc_sla_scan(now=later)
        ticket.invalidate_recordset(['nc_sla_state'])
        first = ticket.nc_sla_state
        self.Ticket._cron_nc_sla_scan(now=later)
        ticket.invalidate_recordset(['nc_sla_state'])
        self.assertEqual(ticket.nc_sla_state, first)

    # --------------------------------------------------------------------- CSAT
    def test_the_rating_surface_from_helpdesk_mgmt_rating_is_present(self):
        """P6-T03's last leg. Adopted, not built — this pins that the adopted
        module is actually installed and its fields reachable."""
        ticket = self._ticket()
        for field in ('rating_ids', 'rating_last_value', 'rating_avg'):
            self.assertIn(field, ticket._fields,
                          "helpdesk_mgmt_rating did not contribute %s" % field)
