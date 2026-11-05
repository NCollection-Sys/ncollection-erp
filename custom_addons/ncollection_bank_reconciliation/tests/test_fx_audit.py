# flake8: noqa: E501  (the expected journal entries read best one line each)
"""Independent foreign-currency audit (OCA/account-reconcile#995).

Company AED, bank journal EUR, transaction EUR, invoice EUR. Rates are set explicitly per date
(1 EUR = 4.00 AED on D1, 4.40 AED on D2), so every expected figure below is computed by hand, not read back:

  invoice 100 EUR on D1  -> receivable 400.00 AED
  received 100 EUR on D2 -> bank 440.00 AED
  realised gain           = 40.00 AED, booked ONCE (in the bank entry or in an exchange entry, never both)

Each scenario prints the journal items it produced (`FXAUDIT` lines) so the report can quote the real entries.
"""
import logging

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import Form, tagged

from .common import VIEW, BankCommon

_logger = logging.getLogger(__name__)
D1 = fields.Date.from_string('2026-03-01')
D2 = fields.Date.from_string('2026-03-20')


@tagged('post_install', '-at_install', 'ncollection_bank_reconciliation')
class TestForeignCurrencyAudit(BankCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.aed = cls.env.ref('base.AED')
        cls.eur = cls.env.ref('base.EUR')
        (cls.aed | cls.eur).active = True
        cls.env['res.currency.rate'].search([('company_id', 'in', (False, cls.company.id))]).unlink()
        cls.company.currency_id = cls.aed
        cls.env['res.currency.rate'].create([
            {'currency_id': cls.eur.id, 'name': D1, 'rate': 0.25, 'company_id': cls.company.id},   # 1 EUR = 4.00 AED
            {'currency_id': cls.eur.id, 'name': D2, 'rate': 1 / 4.4, 'company_id': cls.company.id},  # 1 EUR = 4.40 AED
        ])
        cls.eur_bank = cls.env['account.journal'].create(
            {'name': 'EUR Bank', 'type': 'bank', 'code': 'BEU1', 'currency_id': cls.eur.id})
        cls.eur_bank.suspense_account_id = cls.company.account_journal_suspense_account_id
        cls.gain = cls.company.income_currency_exchange_account_id
        cls.loss = cls.company.expense_currency_exchange_account_id
        cls.suspense = cls.company.account_journal_suspense_account_id

    # ---------------------------------------------------------------- helpers
    def eur_invoice(self, amount, date):
        return self.invoice(amount, currency=self.eur, date=date)

    def eur_line(self, amount, date, partner=True):
        return self.line(amount, journal=self.eur_bank, partner=self.customer if partner else None, date=date,
                         label='EUR receipt')

    def fx_total(self):
        amls = self.env['account.move.line'].search([('account_id', 'in', (self.gain | self.loss).ids),
                                                     ('company_id', '=', self.company.id), ('parent_state', '=', 'posted')])
        return round(-sum(amls.mapped('balance')), 2), amls.move_id

    def suspense_balance(self, st):
        return round(sum(st.move_id.line_ids.filtered(lambda ml: ml.account_id == self.suspense).mapped('balance')), 2)

    def dump(self, tag, moves):
        for move in moves.sorted('id'):
            for ml in move.line_ids.sorted('id'):
                _logger.info('FXAUDIT %s %s %s %s %s balance=%.2f amount_currency=%.2f residual=%.2f/%.2f %s', tag,
                             move.name, move.journal_id.code, ml.account_id.code, ml.currency_id.name, ml.balance,
                             ml.amount_currency, ml.amount_residual, ml.amount_residual_currency,
                             'rec' if ml.reconciled else '')

    def match_all(self, st, inv, amount=None):
        self.match(st, self.open_item(inv), amounts=[amount] if amount is not None else None)
        st.reconcile_bank_line()

    # -------------------------------------------------------------- scenarios
    def test_fx1_same_rate_no_exchange_line(self):
        """Invoice and receipt both on D2: same rate, so NO exchange difference may appear."""
        inv = self.eur_invoice(100.0, D2)
        st = self.eur_line(100.0, D2)
        self.match_all(st, inv)
        fx, moves = self.fx_total()
        self.dump('fx1', st.move_id | inv | moves)
        self.assertEqual(fx, 0.0, 'no exchange difference at an identical rate')
        self.assertFalse(moves, 'no exchange entry at all')
        item = self.open_item(inv)
        self.assertEqual((item.amount_residual, item.amount_residual_currency), (0.0, 0.0))
        self.assertEqual(self.suspense_balance(st), 0.0)
        bank = st.move_id.line_ids.filtered(lambda ml: ml.account_id == self.eur_bank.default_account_id)
        self.assertEqual((bank.balance, bank.amount_currency, bank.currency_id), (440.0, 100.0, self.eur))

    def test_fx2_rate_change_gain_booked_once(self):
        inv = self.eur_invoice(100.0, D1)
        self.assertEqual(self.open_item(inv).balance, 400.0)
        st = self.eur_line(100.0, D2)
        self.match_all(st, inv)
        fx, moves = self.fx_total()
        self.dump('fx2', st.move_id | inv | moves)
        self.assertEqual(fx, 40.0, '440 received - 400 booked = 40.00 AED gain, exactly once')
        item = self.open_item(inv)
        self.assertEqual((item.amount_residual, item.amount_residual_currency), (0.0, 0.0))
        self.assertTrue(item.reconciled)
        self.assertEqual(self.suspense_balance(st), 0.0)
        partner_ar = self.env['account.move.line'].search([('partner_id', '=', self.customer.id),
                                                           ('account_id', '=', self.receivable.id), ('parent_state', '=', 'posted')])
        self.assertEqual(round(sum(partner_ar.mapped('balance')), 2), 0.0, 'the customer owes nothing in AED either')

    def test_fx3_rate_change_loss(self):
        """Mirror: invoice on D2 (440), paid on D1 rate (400) -> 40.00 loss."""
        inv = self.eur_invoice(100.0, D2)
        st = self.eur_line(100.0, D1)
        self.match_all(st, inv)
        fx, moves = self.fx_total()
        self.dump('fx3', st.move_id | inv | moves)
        self.assertEqual(fx, -40.0)
        self.assertEqual(self.open_item(inv).amount_residual, 0.0)

    def test_fx4_partial_payment_proportional_gain(self):
        """60 EUR of a 100 EUR invoice: gain on the settled part only = 60 x 0.40 = 24.00; 40 EUR / 160.00 AED stay open."""
        inv = self.eur_invoice(100.0, D1)
        st = self.eur_line(60.0, D2)
        self.match_all(st, inv)
        fx, moves = self.fx_total()
        self.dump('fx4', st.move_id | inv | moves)
        self.assertEqual(fx, 24.0)
        item = self.open_item(inv)
        self.assertEqual((round(item.amount_residual, 2), round(item.amount_residual_currency, 2)), (160.0, 40.0))
        self.assertEqual(self.suspense_balance(st), 0.0)

    def test_fx5_repeated_attempt_adds_nothing(self):
        inv = self.eur_invoice(100.0, D1)
        st = self.eur_line(100.0, D2)
        self.match_all(st, inv)
        before, _moves = self.fx_total()
        with self.assertRaises(UserError):
            st.reconcile_bank_line()
        after, moves = self.fx_total()
        self.dump('fx5', moves)
        self.assertEqual((before, after), (40.0, 40.0), 'a refused second attempt books no second difference')

    def test_fx6_undo_and_rematch_net_once(self):
        inv = self.eur_invoice(100.0, D1)
        st = self.eur_line(100.0, D2)
        self.match_all(st, inv)
        self.assertEqual(self.fx_total()[0], 40.0)
        st.unreconcile_bank_line()
        undone, moves = self.fx_total()
        self.dump('fx6-undo', st.move_id | moves)
        self.assertEqual(undone, 0.0, 'undo takes the realised difference back out')
        item = self.open_item(inv)
        self.assertEqual((item.amount_residual, item.amount_residual_currency), (400.0, 100.0))
        self.match_all(st, inv)
        again, moves = self.fx_total()
        self.dump('fx6-rematch', st.move_id | moves)
        self.assertEqual(again, 40.0, 'rematch: the difference is booked once again, not twice')
        self.assertEqual(self.open_item(inv).amount_residual, 0.0)

    def test_fx7_two_receipts_two_rates(self):
        """50 EUR on D1 (rate of the invoice: no difference) + 50 EUR on D2 (20.00 gain)."""
        inv = self.eur_invoice(100.0, D1)
        st1 = self.eur_line(50.0, D1)
        self.match_all(st1, inv)
        self.assertEqual(self.fx_total()[0], 0.0)
        st2 = self.eur_line(50.0, D2)
        self.match_all(st2, inv)
        fx, moves = self.fx_total()
        self.dump('fx7', st1.move_id | st2.move_id | moves)
        self.assertEqual(fx, 20.0)
        self.assertEqual(self.open_item(inv).amount_residual, 0.0)

    def test_fx8_company_currency_journal_receives_foreign_invoice(self):
        """AED bank receives 440 AED for a 100 EUR invoice (booked 400): the AED line carries the EUR amount as its
        foreign currency; the 40.00 gain is booked once."""
        aed_bank = self.misr
        inv = self.eur_invoice(100.0, D1)
        st = self.line(440.0, journal=aed_bank, partner=self.customer, date=D2, foreign_currency_id=self.eur.id,
                       amount_currency=100.0)
        self.match_all(st, inv)
        fx, moves = self.fx_total()
        self.dump('fx8', st.move_id | moves)
        self.assertEqual(fx, 40.0)
        self.assertEqual(self.open_item(inv).amount_residual_currency, 0.0)

    def test_fx9_ledger_after_all(self):
        """Ledger cross-check on one fresh scenario: bank 440 = receivable 400 + gain 40; suspense 0."""
        inv = self.eur_invoice(100.0, D1)
        st = self.eur_line(100.0, D2)
        self.match_all(st, inv)
        lines = self.env['account.move.line'].search([('company_id', '=', self.company.id), ('parent_state', '=', 'posted'),
                                                      ('move_id', 'in', (st.move_id | inv | self.fx_total()[1]).ids)])
        by = {}
        for ml in lines:
            by[ml.account_id.code] = round(by.get(ml.account_id.code, 0.0) + ml.balance, 2)
        _logger.info('FXAUDIT fx9 ledger %s', by)
        self.assertEqual(round(sum(by.values()), 2), 0.0)
        self.assertEqual(by.get(self.eur_bank.default_account_id.code), 440.0)
        self.assertEqual(by.get(self.receivable.code, 0.0), 0.0)
        self.assertEqual(by.get(self.suspense.code, 0.0), 0.0)
