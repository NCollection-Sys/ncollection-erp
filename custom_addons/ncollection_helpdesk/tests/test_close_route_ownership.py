# -*- coding: utf-8 -*-
"""The adopted /ticket/close route may not close someone else's ticket (#67).

WHY THIS FILE IS SEPARATE FROM THE ORM ISOLATION TESTS. `test_ticket_portal_
isolation.py` proves the record rule holds at the ORM — and it would have
passed with this hole wide open, because the hole is not at the ORM. OCA's
`/ticket/close` controller looks the ticket up with `.sudo()` from a
request-supplied id, so the rule is never consulted at all. An ORM-only suite
is structurally incapable of seeing that, which is precisely why the isolation
audit found it and the tests did not.

So this is an HttpCase: it drives the real route over HTTP, as a real logged-in
portal user, which is the only layer where the bug exists.

Both directions are asserted, deliberately:
  * the OWNER can still close their own ticket (a guard that breaks the feature
    is not a fix), and
  * a foreign portal user cannot.
"""
from odoo import http
from odoo.tests import HttpCase, tagged


@tagged('post_install', '-at_install')
class TestCloseRouteOwnership(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        portal_group = cls.env.ref('base.group_portal')

        def customer(tag):
            partner = cls.env['res.partner'].create({'name': 'Close %s' % tag})
            user = cls.env['res.users'].create({
                'name': 'closer %s' % tag,
                'login': 'nc_closer_%s' % tag,
                'password': 'nc_closer_%s' % tag,
                'email': 'closer_%s@t.test' % tag,
                'partner_id': partner.id,
                'group_ids': [(6, 0, [portal_group.id])],
            })
            ticket = cls.env['helpdesk.ticket'].create({
                'name': 'Ticket %s' % tag, 'description': 'b',
                'partner_id': partner.id})
            return user, ticket

        cls.user_a, cls.ticket_a = customer('a')
        cls.user_b, cls.ticket_b = customer('b')

        # A stage the portal is actually allowed to close into — without one
        # the route is a no-op and every assertion below would pass vacuously.
        cls.close_stage = cls.env['helpdesk.ticket.stage'].search(
            [('close_from_portal', '=', True)], limit=1)
        if not cls.close_stage:
            cls.close_stage = cls.env['helpdesk.ticket.stage'].search(
                [('closed', '=', True)], limit=1)
            cls.close_stage.close_from_portal = True

    def _post_close(self, ticket):
        # `http.Request.csrf_token(self)` is the idiom Odoo's own HttpCase
        # tests use (account/tests/test_portal_attachment.py and others): the
        # test case supplies the .session and .env the method needs. Written
        # without it first, every POST came back 400 CSRF — which meant the
        # isolation assertions were passing because NOTHING happened. The
        # owner-can-still-close control is what exposed that.
        return self.url_open('/ticket/close', data={
            'ticket_id': str(ticket.id),
            'stage_id': str(self.close_stage.id),
            'csrf_token': http.Request.csrf_token(self),
        }, allow_redirects=False)

    def test_the_owner_can_still_close_their_own_ticket(self):
        """The control. A guard that also breaks the legitimate path is not a
        fix, and without this the isolation assertion below could pass simply
        because the route stopped working for everyone."""
        self.authenticate('nc_closer_a', 'nc_closer_a')
        self._post_close(self.ticket_a)
        self.ticket_a.invalidate_recordset(['stage_id'])
        self.assertEqual(self.ticket_a.stage_id, self.close_stage)

    def test_a_portal_user_cannot_close_another_customers_ticket(self):
        """The CRITICAL from the isolation audit, pinned.

        Before the override in controllers/main.py this succeeded: the route
        is auth="user" and resolves the ticket with .sudo(), so the portal
        record rule never ran and customer A closed customer B's ticket.
        """
        stage_before = self.ticket_b.stage_id
        self.authenticate('nc_closer_a', 'nc_closer_a')
        self._post_close(self.ticket_b)
        self.ticket_b.invalidate_recordset(['stage_id'])
        self.assertEqual(
            self.ticket_b.stage_id, stage_before,
            "a portal user closed another customer's ticket via /ticket/close")

    def test_the_sla_state_of_a_foreign_ticket_is_not_touched_either(self):
        """The side effect that made the IDOR worse: this module's write()
        reacts to stage_id, so a successful foreign close would also stamp the
        victim's first response and rewrite their SLA verdict."""
        before = self.ticket_b.nc_sla_first_response
        self.authenticate('nc_closer_a', 'nc_closer_a')
        self._post_close(self.ticket_b)
        self.ticket_b.invalidate_recordset(['nc_sla_first_response'])
        self.assertEqual(self.ticket_b.nc_sla_first_response, before)
