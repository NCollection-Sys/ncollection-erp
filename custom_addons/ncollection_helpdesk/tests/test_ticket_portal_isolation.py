# -*- coding: utf-8 -*-
"""Tickets are isolated between portal customers (P6-T03 / #67).

THIS FILE IS #66's HANDOFF. P6-T02 proved portal isolation for invoices, sale
orders and deliveries, and recorded that it could not cover tickets because no
ticket model existed: *"The ticket also names 'tickets' — no such model exists,
and P6-T03 (#67), which builds it, DEPENDS on this task."* This closes that.

WHAT IS BEING PROVEN, AND WHOSE CODE. The record rule under test ships with OCA
`helpdesk_mgmt` ("Portal Personal Tickets": `partner_id child_of
user.commercial_partner_id`, or being a follower). NCollection wrote no rule
here — and that is exactly why these assertions matter. An adopted dependency's
isolation is still OUR isolation the moment we ship it: a future OCA bump could
weaken that domain and nothing else in this repo would notice.

The house style from #66 is followed deliberately:
  * every isolation assertion is paired with a CONTROL that would fail if the
    fixture were empty — a test that passes because there is no data proves
    nothing;
  * the IDOR shape (direct read of a KNOWN id) is asserted separately from
    search filtering, because they fail independently.
"""
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestTicketPortalIsolation(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Ticket = cls.env['helpdesk.ticket']
        portal_group = cls.env.ref('base.group_portal')

        def customer(tag):
            partner = cls.env['res.partner'].create({'name': 'HD %s' % tag})
            user = cls.env['res.users'].create({
                'name': 'portal %s' % tag,
                'login': 'hd_portal_%s' % tag,
                'email': 'hd_%s@t.test' % tag,
                'partner_id': partner.id,
                # Odoo 19: the groups relation is group_ids, not groups_id.
                'group_ids': [(6, 0, [portal_group.id])],
            })
            ticket = cls.Ticket.create({
                'name': 'Ticket for %s' % tag,
                'description': 'body %s' % tag,
                'partner_id': partner.id,
            })
            return partner, user, ticket

        cls.partner_a, cls.user_a, cls.ticket_a = customer('a')
        cls.partner_b, cls.user_b, cls.ticket_b = customer('b')

    # ------------------------------------------------------------------ controls
    def test_control_an_internal_user_sees_both_tickets(self):
        """Guards vacuity: if this fails the fixture is broken and every
        isolation assertion below would pass for the wrong reason."""
        seen = self.Ticket.search([]).ids
        self.assertIn(self.ticket_a.id, seen)
        self.assertIn(self.ticket_b.id, seen)

    def test_control_a_portal_user_sees_its_OWN_ticket(self):
        """The other half of the vacuity guard: isolation that hides
        everything is not isolation, it is a broken portal."""
        seen = self.Ticket.with_user(self.user_a).search([]).ids
        self.assertIn(self.ticket_a.id, seen)

    # ----------------------------------------------------------------- isolation
    def test_a_portal_user_cannot_search_another_customers_ticket(self):
        seen = self.Ticket.with_user(self.user_a).search([]).ids
        self.assertNotIn(self.ticket_b.id, seen)

    def test_a_portal_user_cannot_read_another_customers_ticket_by_id(self):
        """The IDOR shape — the actual threat. Filtering a list is not the
        same control as refusing a direct read of a known id, and they fail
        independently, so this is asserted on its own."""
        with self.assertRaises(AccessError):
            self.Ticket.with_user(self.user_a).browse(
                self.ticket_b.id).read(['name'])

    def test_a_portal_user_cannot_write_another_customers_ticket(self):
        with self.assertRaises(AccessError):
            self.Ticket.with_user(self.user_a).browse(
                self.ticket_b.id).write({'name': 'hijacked'})

    def test_a_portal_user_cannot_unlink_another_customers_ticket(self):
        with self.assertRaises(AccessError):
            self.Ticket.with_user(self.user_a).browse(self.ticket_b.id).unlink()

    def test_a_portal_user_cannot_read_another_customers_ticket_chatter(self):
        """#66 measured group_portal as having mail.message r=1: access to a
        message is gated by access to its DOCUMENT, so the ticket rule is what
        stops chatter leaking. Asserted rather than assumed."""
        self.ticket_b.message_post(body='internal note for B')
        with self.assertRaises(AccessError):
            self.Ticket.with_user(self.user_a).browse(
                self.ticket_b.id).read(['message_ids'])

    def test_the_sla_columns_do_not_leak_across_customers(self):
        """The fields THIS module adds must inherit the same isolation. A new
        column on a shared model is the classic way a proven boundary springs
        a fresh leak."""
        with self.assertRaises(AccessError):
            self.Ticket.with_user(self.user_a).browse(self.ticket_b.id).read(
                ['nc_sla_state', 'nc_sla_resolution_deadline'])

    def test_the_isolation_rule_is_still_the_one_we_reviewed(self):
        """Pins the ADOPTED domain. If an OCA bump rewrites this rule, the
        change surfaces here as a failing test rather than as a silent
        widening of who can read a customer's tickets."""
        rule = self.env.ref('helpdesk_mgmt.helpdesk_ticket_rule_portal',
                            raise_if_not_found=False)
        self.assertTrue(rule, "helpdesk_mgmt no longer ships a portal rule")
        self.assertIn('commercial_partner_id', rule.domain_force)
        self.assertIn('child_of', rule.domain_force)
