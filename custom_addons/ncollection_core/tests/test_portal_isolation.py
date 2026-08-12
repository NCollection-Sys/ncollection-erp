# -*- coding: utf-8 -*-
"""Portal users see ONLY their own records (P6-T02 / #66).

WHAT THIS PROVES, AND WHAT IT DOES NOT. Odoo 19 Community already ships correct
portal isolation — verified against the live rules rather than the docs:

    account.move    state not in (cancel,draft) AND move_type in (out_*,in_*)
                    AND partner_id child_of user.commercial_partner_id
    sale.order      partner_id child_of user.commercial_partner_id
    stock.picking   partner_id = user.partner_id
                    OR sale_id.partner_id = user.partner_id   (from sale_stock)

So #66 is a TEST ticket, not a rules ticket: nothing here adds an ir.rule. What
was missing was any proof, and proof that can fail. These tests are that.

THE CONTROL IS THE POINT. Every isolation assertion here is paired with one
showing the record IS visible to somebody — the owning portal user, and an
internal user. Without that, "portal A sees none of B's invoices" is satisfied
just as well by a database with no invoices in it, which is exactly the state
e2eclienta was in before this ticket: zero invoices, and the only "portal user"
was Odoo's own `portaltemplate`. This repo has shipped four vacuous guards
(#330, #348, #363, #381); this one states its control out loud.

ONE CONTROL POINT, NOT TWO. ir.rule is enforced inside the ORM for browser
HTTP, JSON-RPC and XML-RPC alike, so proving isolation through `search`/`read`
here also covers the URL-manipulation vector the ticket describes. The Playwright
spec (`e2e/tests/portal-isolation.spec.ts`) still exercises the HTTP surface,
because the controller layer adds `_document_check_access` and an access_token
fallback that the ORM alone does not describe.

SCOPE. Invoices, orders and deliveries. The ticket also names "tickets": no
ticket model exists, and the task that builds it (P6-T03, #67) DEPENDS on this
one, so the requirement was handed there rather than dropped. See #66's comment.
"""

from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged

_MODELS = ('account.move', 'sale.order', 'stock.picking')


@tagged('post_install', '-at_install')
class TestPortalIsolation(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        missing = [m for m in _MODELS if m not in cls.env]
        if missing:
            # A scoped run (make test m=ncollection_core) installs this module
            # alone, so sale/account/stock are absent and there is genuinely
            # nothing to isolate. Skip with the reason rather than assert — the
            # #374 lesson: turning a supported workflow red is worse than the
            # vacuity it guards against. The skip gate makes it visible, and in
            # CI's full matrix these models are installed so it does not skip.
            raise cls.skipTest(cls, 'portal isolation needs %s' % ', '.join(missing))

        cls.portal_group = cls.env.ref('base.group_portal')
        cls.product = cls.env['product.product'].create({
            'name': 'P6T02 Test Widget', 'type': 'consu', 'is_storable': True,
            'list_price': 100.0,
        })
        cls.alpha = cls._make_party('Alpha Test Ltd', 'p6t02_alpha')
        cls.beta = cls._make_party('Beta Test Ltd', 'p6t02_beta')

    @classmethod
    def _make_party(cls, company, login):
        """A partner + its portal user + one invoice, one order, one delivery."""
        partner = cls.env['res.partner'].create({'name': company})
        user = cls.env['res.users'].create({
            'name': company, 'login': login, 'password': 'p6t02pw!',
            'partner_id': partner.id,
            # Odoo 19 renamed res.users.groups_id -> group_ids.
            'group_ids': [(6, 0, [cls.portal_group.id])],
        })
        invoice = cls.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': partner.id,
            'invoice_line_ids': [(0, 0, {
                'product_id': cls.product.id, 'quantity': 1,
                'price_unit': 100.0,
            })],
        })
        # POSTED is load-bearing: the core rule excludes draft, so a draft
        # invoice is invisible even to its owner and the control below would
        # fail for a reason unrelated to isolation.
        invoice.action_post()
        order = cls.env['sale.order'].create({
            'partner_id': partner.id,
            'order_line': [(0, 0, {
                'product_id': cls.product.id, 'product_uom_qty': 1,
            })],
        })
        # Confirming is what makes sale_stock generate the delivery, so the
        # picking carries sale_id — the branch a real delivery actually takes.
        order.action_confirm()
        return {
            'partner': partner, 'user': user, 'invoice': invoice,
            'order': order, 'pickings': order.picking_ids,
        }

    # -- controls: the records exist and ARE visible to the right people ----

    def test_control_an_internal_user_sees_both_parties_records(self):
        """If this fails, every isolation assertion below is vacuous."""
        self.assertTrue(self.alpha['invoice'].exists())
        self.assertTrue(self.beta['invoice'].exists())
        both = [self.alpha['partner'].id, self.beta['partner'].id]
        seen = self.env['account.move'].search([('partner_id', 'in', both)])
        self.assertEqual(len(seen), 2, "an internal user must see both invoices")

    def test_control_a_portal_user_sees_its_OWN_records(self):
        """The other half of the control: the portal user is not blind."""
        a = self.alpha
        for model, key in (('account.move', 'invoice'), ('sale.order', 'order')):
            with self.subTest(model=model):
                own = self.env[model].with_user(a['user']).search(
                    [('partner_id', '=', a['partner'].id)])
                self.assertIn(a[key].id, own.ids,
                              "%s: the owner cannot see their own record, so a "
                              "'cannot see the other's' assertion proves "
                              "nothing" % model)
        if a['pickings']:
            own = self.env['stock.picking'].with_user(a['user']).search(
                [('id', 'in', a['pickings'].ids)])
            self.assertTrue(own, "the owner cannot see their own delivery")

    # -- isolation ---------------------------------------------------------

    def test_a_portal_user_cannot_search_another_partners_records(self):
        for model, key in (('account.move', 'invoice'), ('sale.order', 'order')):
            with self.subTest(model=model):
                leaked = self.env[model].with_user(self.alpha['user']).search(
                    [('partner_id', '=', self.beta['partner'].id)])
                self.assertFalse(
                    leaked, "%s: portal user A retrieved partner B's records "
                            "(#66 — zero cross-partner leakage)" % model)

    def test_a_portal_user_cannot_search_another_partners_deliveries(self):
        """NOT proved by the same mutation as the invoice/order assertions.

        stock.picking's rule is `partner_id = user.partner_id` OR
        `sale_id.partner_id = user.partner_id` — an EXACT match, where
        account.move and sale.order use `child_of commercial_partner_id`. So
        re-parenting one partner under the other (which breaks the other four
        assertions) cannot leak a delivery, and this assertion stays green
        through it. That difference is real and is the one inconsistency the
        #66 survey found in core; it is the stricter direction, so it can
        under-expose a legitimate sibling contact but never over-expose.

        Mutation-proved separately by pointing Beta's delivery at Alpha's
        partner, which this assertion then catches.
        """
        if not self.beta['pickings']:
            self.skipTest('no delivery generated — sale_stock may be absent')
        leaked = self.env['stock.picking'].with_user(self.alpha['user']).search(
            [('id', 'in', self.beta['pickings'].ids)])
        self.assertFalse(leaked, "portal user A retrieved partner B's delivery")

    def test_the_IDOR_shape_a_direct_read_of_a_known_id_is_denied(self):
        """The ticket's actual threat: not a search, a guessed record id.

        `search` returning nothing could in principle be a filtered query; a
        direct `browse().read()` cannot be explained away, which is why it is
        asserted separately.
        """
        for model, key in (('account.move', 'invoice'), ('sale.order', 'order')):
            with self.subTest(model=model):
                target = self.beta[key]
                with self.assertRaises(AccessError):
                    self.env[model].with_user(
                        self.alpha['user']).browse(target.id).read(['id'])

    # -- vectors #403: attachments, chatter, followers ---------------------

    def _attach(self, party, public=False):
        """An attachment hung on that party's invoice."""
        return self.env['ir.attachment'].create({
            'name': 'p6t02-%s.txt' % party['partner'].name,
            'raw': b'confidential',
            'res_model': 'account.move', 'res_id': party['invoice'].id,
            'public': public,
        })

    def test_a_portal_user_has_no_ORM_access_to_attachments_at_all(self):
        """#403: measured, and it is NOT what the code comments suggest.

        `ir_attachment._check_access` reads "public is always accessible" and
        otherwise defers to the referenced record — which implies a portal user
        could read attachments on their own documents. They cannot: group_portal
        has NO ir.model.access row for ir.attachment, so the model-level check
        denies first and the record-level logic is never reached. Verified on a
        live tenant: the ACL table has rows for mail.message but none for
        ir.attachment or mail.followers.

        So an ORM assertion here cannot demonstrate cross-partner isolation —
        it demonstrates that portal users have no ORM path to attachments in
        EITHER direction. That is the honest claim, and it is worth pinning:
        if a future module grants group_portal read on ir.attachment, this
        fails and the real isolation question (which the controller answers)
        becomes live.

        The actual attachment-serving path is the portal controller, which uses
        sudo plus `_document_check_access`. That is exercised over HTTP in
        e2e/tests/portal-isolation.spec.ts, not here.
        """
        mine = self._attach(self.alpha)
        theirs = self._attach(self.beta)
        for label, att, user in (('own', mine, self.alpha['user']),
                                 ("another partner's", theirs, self.alpha['user'])):
            with self.subTest(attachment=label):
                with self.assertRaises(
                        AccessError,
                        msg="portal gained ORM access to %s attachment; the "
                            "controller path is now the only thing isolating "
                            "them and needs its own test" % label):
                    self.env['ir.attachment'].with_user(user).browse(
                        att.id).read(['name'])

    def test_attachments_on_financial_documents_default_to_not_public(self):
        """`public=True` is a one-field bypass, so the default is worth pinning.

        `_check_access`: "public is always accessible for reading" — that check
        sits BELOW the model ACL, so today it is unreachable for portal users
        via the ORM. It is reachable through the controller path, and nothing in
        account/sale/sale_stock sets public=True on report output (grepped).
        This fails if that ever changes.
        """
        self.assertFalse(self._attach(self.alpha).public)

    def test_a_portal_user_cannot_read_chatter_on_another_partners_invoice(self):
        """mail.message inherits the document's access. Verified once by hand
        during #66's review; this makes it permanent."""
        self.beta['invoice'].message_post(body='Beta internal note')
        msgs = self.env['mail.message'].with_user(self.alpha['user']).search(
            [('model', '=', 'account.move'),
             ('res_id', '=', self.beta['invoice'].id)])
        self.assertFalse(msgs, "portal user A read chatter on B's invoice")

    def test_a_portal_user_cannot_enumerate_followers_of_another_partners_invoice(self):
        """mail.followers leaks WHO is involved even when the body does not."""
        # Measured: group_portal has no ir.model.access row for mail.followers,
        # so this RAISES rather than returning an empty set. Asserting
        # `assertFalse(search(...))` would have been wrong in a way that still
        # looked like a pass in a less careful reading.
        with self.assertRaises(AccessError):
            self.env['mail.followers'].with_user(self.alpha['user']).search(
                [('res_model', '=', 'account.move'),
                 ('res_id', '=', self.beta['invoice'].id)])

    def test_a_portal_user_cannot_write_to_another_partners_record(self):
        """Read isolation without write isolation would still be a breach.

        MEASURED CAVEAT: this passes because of the model ACL, not the record
        rule. `sale.order.portal` is r=true w=false c=false u=false, so writes
        are refused before any domain is consulted — proved by the mutation
        below, which broke the read assertions and left this one green.

        Worth asserting as defence in depth, but it must not be read as
        evidence that the record rule isolates writes: it would pass unchanged
        if that rule leaked completely.
        """
        with self.assertRaises(AccessError):
            self.env['sale.order'].with_user(self.alpha['user']).browse(
                self.beta['order'].id).write({'client_order_ref': 'pwned'})

    def test_a_portal_user_cannot_unlink_another_partners_record(self):
        with self.assertRaises(AccessError):
            self.env['sale.order'].with_user(self.alpha['user']).browse(
                self.beta['order'].id).unlink()
