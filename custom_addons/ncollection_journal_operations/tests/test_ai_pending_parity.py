# -*- coding: utf-8 -*-
"""The AI Assistant's pending figures ARE the card's (final review #6): the same outstanding-account residuals, per
journal, incoming and outgoing apart. The AI topic spec is run exactly as the assistant runs it (a read_group as the
asking user); it is imported from the ncollection_ai source, so no dependency between the two modules is needed."""
from odoo import fields
from odoo.tests import new_test_user, tagged

from odoo.addons.ncollection_ai.models import ai_topics

from .common import JournalOpsCommon


@tagged('post_install', '-at_install')
class TestAiPendingParity(JournalOpsCommon):

    def _ai(self, env=None):
        env = env or self.env
        out = {}
        for spec in ai_topics._bank_cash(fields.Date.today()):
            if spec['key'] not in ('receipts_pending_bank', 'payments_pending_bank'):
                continue
            for journal, residual in env[spec['model']]._read_group(spec['domain'], spec['groupby'],
                                                                    spec['aggregates']):
                side = 'pending_in' if spec['key'] == 'receipts_pending_bank' else 'pending_out'
                out.setdefault(journal.id, {'pending_in': 0.0, 'pending_out': 0.0})[side] += residual
        return out

    def _assert_parity(self, journals):
        ai = self._ai()
        for journal in journals:
            card = self._figures(journal)
            got = ai.get(journal.id, {'pending_in': 0.0, 'pending_out': 0.0})
            self.assertAlmostEqual(got['pending_in'], card['pending_in'], msg='%s in' % journal.name)
            self.assertAlmostEqual(got['pending_out'], card['pending_out'], msg='%s out' % journal.name)

    def test_paid_but_unconfirmed_receipt_is_pending_in_both(self):
        invoice = self._invoice(self.customer, 200.0)
        wizard = self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=invoice.ids).create({'journal_id': self.bank.id})
        wizard._create_payments()
        self.assertEqual(self._ai()[self.bank.id]['pending_in'], 200.0, "Odoo says paid, the bank has not confirmed")
        self._assert_parity(self.bank | self.cash)

    def test_in_process_partial_and_another_journal(self):
        receipt = self._op(self.bank, 'receipt', 'customer', 100.0, self.customer)
        outstanding = receipt.move_id.line_ids.filtered(lambda line: line.debit)
        st_line = self.env['account.bank.statement.line'].create({
            'journal_id': self.bank.id, 'date': fields.Date.today(), 'payment_ref': 'part', 'amount': 40.0,
            'counterpart_account_id': outstanding.account_id.id})
        (st_line.move_id.line_ids.filtered(lambda line: line.account_id == outstanding.account_id) | outstanding) \
            .reconcile()
        self._op(self.bank, 'payment', 'supplier', 30.0, self.supplier)
        self._op(self.cash, 'payment', 'account', 12.0, account=self.fees)
        self._assert_parity(self.bank | self.cash | self.bank2)
        self.assertAlmostEqual(self._ai()[self.bank.id]['pending_in'], 60.0, msg="partial: only the remainder")

    def test_confirmed_money_is_no_longer_pending(self):
        receipt = self._op(self.bank, 'receipt', 'customer', 25.0, self.customer)
        receipt._nc_mark_as_paid()
        self.assertNotIn(self.bank.id, self._ai())
        self._assert_parity(self.bank)

    def test_foreign_currency_counts_in_company_currency_like_the_card(self):
        eur = self.setup_other_currency('EUR', rates=[('2016-01-01', 2.0)])
        self._op(self.bank, 'receipt', 'customer', 50.0, self.customer, currency_id=eur.id)
        self.assertAlmostEqual(self._ai()[self.bank.id]['pending_in'], 25.0, msg="50 EUR at 2.0 = 25 company")
        self._assert_parity(self.bank)

    def test_a_user_without_accounting_rights_gets_nothing(self):
        self._op(self.bank, 'receipt', 'customer', 10.0, self.customer)
        plain = new_test_user(self.env, login='nc_ai_parity_plain', groups='base.group_user')
        try:
            figures = self._ai(self.env(user=plain))
        except Exception as exc:  # AccessError: the engine drops a spec the user cannot read
            figures = {'refused': type(exc).__name__}
        self.assertNotIn(self.bank.id, figures)
