# -*- coding: utf-8 -*-
"""F2-T05: Customer / Vendor Statements — per-partner documents that agree.

The statement inherits the Partner Ledger's arithmetic rather than repeating it,
so the assertion that matters is that they cannot diverge: a statement whose
"Balance Due" disagrees with the ledger it was derived from is worse than no
statement, because it is the number a customer is asked to pay.
"""
from datetime import date

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged


@tagged('post_install', '-at_install')
class TestPartnerStatement(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.St = cls.env['ncollection.account.report.statement']
        cls.PL = cls.env['ncollection.account.report.partner.ledger']
        cls.receivable = cls.company_data['default_account_receivable']
        cls.revenue = cls.company_data['default_account_revenue']
        cls.journal = cls.company_data['default_journal_misc']

        def entry(day, amount, partner):
            move = cls.env['account.move'].create({
                'move_type': 'entry', 'journal_id': cls.journal.id, 'date': day,
                'line_ids': [
                    (0, 0, {'account_id': cls.receivable.id, 'debit': amount,
                            'credit': 0.0, 'partner_id': partner.id}),
                    (0, 0, {'account_id': cls.revenue.id, 'debit': 0.0,
                            'credit': amount, 'partner_id': partner.id}),
                ]})
            move.action_post()

        entry(date(2025, 12, 1), 200.0, cls.partner_a)     # before the window
        entry(date(2026, 2, 1), 500.0, cls.partner_a)
        entry(date(2026, 5, 1), 300.0, cls.partner_a)
        entry(date(2026, 3, 1), 900.0, cls.partner_b)
        cls.date_from = date(2026, 1, 1)
        cls.date_to = date(2026, 12, 31)

    def _st(self, **kw):
        vals = {'date_from': self.date_from, 'date_to': self.date_to,
                'target_move': 'posted', 'partner_scope': 'receivable'}
        vals.update(kw)
        return self.St.create(vals)

    def _doc(self, wizard, partner):
        docs = [d for d in wizard._nc_statement_docs()
                if d['partner'].id == partner.id]
        self.assertEqual(len(docs), 1, "expected one statement for the partner")
        return docs[0]

    # ---- one document per partner --------------------------------------

    def test_one_document_per_partner(self):
        """A statement is addressed to a partner. Producing a single combined
        document would make it unsendable, which is the whole acceptance."""
        docs = self._st()._nc_statement_docs()
        self.assertEqual({d['partner'].id for d in docs},
                         {self.partner_a.id, self.partner_b.id})

    def test_opening_closing_and_movements_are_separated(self):
        """200 before the window, then 500 + 300 inside it."""
        doc = self._doc(self._st(), self.partner_a)
        self.assertAlmostEqual(doc['opening'], 200.0, places=2)
        self.assertAlmostEqual(doc['closing'], 1000.0, places=2)
        self.assertEqual(len(doc['lines']), 2,
                         "the opening row leaked into the movement list, or a "
                         "movement was dropped")
        self.assertNotIn(True, [ln['is_initial'] for ln in doc['lines']])

    # ---- the assertion that matters ------------------------------------

    def test_balance_due_equals_the_partner_ledger_closing_balance(self):
        """The number the customer is asked to pay must be the ledger's.

        They share an implementation by inheritance, so this is a guard against
        someone later "optimising" the statement into its own calculation.
        """
        ledger = self.PL.create({
            'date_from': self.date_from, 'date_to': self.date_to,
            'target_move': 'posted', 'partner_scope': 'receivable'})
        ledger_rows = [r for r in ledger._nc_partner_rows()
                       if r['partner_id'] == self.partner_a.id]
        self.assertAlmostEqual(
            self._doc(self._st(), self.partner_a)['closing'],
            ledger_rows[-1]['running_balance'], places=2,
            msg="the statement's Balance Due and the partner ledger disagree")

    def test_a_partner_filter_produces_only_that_partners_statement(self):
        docs = self._st(partner_ids=[(6, 0, [self.partner_b.id])]
                        )._nc_statement_docs()
        self.assertEqual([d['partner'].id for d in docs], [self.partner_b.id])

    # ---- sending -------------------------------------------------------

    def test_send_opens_a_draft_with_the_pdf_attached(self):
        """Sendable means a composer with the PDF ready — NOT a silent send.

        Asserting the returned action is the composer is what pins that
        decision; a future change that dispatched mail directly would fail here.
        """
        action = self._st().action_send()
        self.assertEqual(action['res_model'], 'mail.compose.message')
        ctx = action['context']
        self.assertIn(self.partner_a.id, ctx['default_partner_ids'])
        attachment_ids = ctx['default_attachment_ids'][0][2]
        attachment = self.env['ir.attachment'].browse(attachment_ids)
        self.assertEqual(attachment.mimetype, 'application/pdf')
        self.assertTrue(attachment.raw, "an empty PDF was attached")
        self.assertFalse(attachment.public,
                         "the statement attachment is public — anyone guessing "
                         "the id could read another partner's balances")

    def test_a_period_with_no_movements_still_states_the_balance(self):
        """"No activity, you still owe X" is a legitimate statement.

        My first version of this test asserted a REFUSAL for a period after all
        the data, and it failed — correctly. Every earlier entry becomes the
        opening balance, so such a statement is not empty and refusing to send
        it would withhold the one number the customer cares about.
        """
        docs = self._st(date_from=date(2030, 1, 1),
                        date_to=date(2030, 12, 31))._nc_statement_docs()
        doc = [d for d in docs if d['partner'].id == self.partner_a.id][0]
        self.assertEqual(doc['lines'], [], "expected no movements in 2030")
        self.assertAlmostEqual(doc['closing'], 1000.0, places=2)

    def test_send_refuses_when_there_is_genuinely_nothing(self):
        """A window BEFORE any data: no opening, no movements, no partners.

        This is the real empty case — a blank document addressed to a customer
        is worse than an error.
        """
        with self.assertRaises(UserError):
            self._st(date_from=date(2020, 1, 1),
                     date_to=date(2020, 12, 31)).action_send()

    def test_a_user_without_the_accounting_group_is_denied(self):
        user = self.env['res.users'].create({
            'name': 'No Accounting', 'login': 'f2t05_stmt_noacc',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])],
        })
        with self.assertRaises(AccessError):
            self.St.with_user(user).create({
                'date_from': self.date_from, 'date_to': self.date_to,
                'target_move': 'posted'})
