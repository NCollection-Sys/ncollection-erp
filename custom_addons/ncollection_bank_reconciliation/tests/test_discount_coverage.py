# flake8: noqa: E501  (hand-computed figures read best one per line)
"""Early-payment discount accounting coverage: every mode, foreign currency, the window, credit notes, vendor bills.

The expected figures are computed by hand from the invoice and the rates (written in each test), then compared with
the journal items Odoo's own helpers booked; none is read back from the code under test to prove itself.

AED company, 5 % VAT, a 2 % early-payment term. Rates for the foreign-currency cases: 1 EUR = 4.00 AED on D1 (the
invoice) and 4.40 AED on D2 (the receipt).
"""
from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import BankCommon

D1 = fields.Date.from_string('2026-03-01')
D2 = fields.Date.from_string('2026-03-20')


@tagged('post_install', '-at_install', 'ncollection_bank_reconciliation')
class TestDiscountCoverage(BankCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.aed = cls.env.ref('base.AED')
        cls.eur = cls.env.ref('base.EUR')
        (cls.aed | cls.eur).active = True
        cls.env['res.currency.rate'].search([('company_id', 'in', (False, cls.company.id))]).unlink()
        cls.company.currency_id = cls.aed
        cls.env['res.currency.rate'].create([
            {'currency_id': cls.eur.id, 'name': D1, 'rate': 0.25, 'company_id': cls.company.id},
            {'currency_id': cls.eur.id, 'name': D2, 'rate': 1 / 4.4, 'company_id': cls.company.id},
        ])
        cls.eur_bank = cls.env['account.journal'].create(
            {'name': 'EUR Bank', 'type': 'bank', 'code': 'BEU2', 'currency_id': cls.eur.id})
        cls.eur_bank.suspense_account_id = cls.company.account_journal_suspense_account_id
        cls.sale_tax = cls.env['account.tax'].create({'name': 'VAT 5% sale (cov)', 'amount': 5.0, 'type_tax_use': 'sale'})
        cls.purchase_tax = cls.env['account.tax'].create(
            {'name': 'VAT 5% purchase (cov)', 'amount': 5.0, 'type_tax_use': 'purchase'})
        cls.gain = cls.company.income_currency_exchange_account_id
        cls.loss = cls.company.expense_currency_exchange_account_id
        cls.discount_loss = cls.company.account_journal_early_pay_discount_loss_account_id
        cls.discount_gain = cls.company.account_journal_early_pay_discount_gain_account_id

    # -------------------------------------------------------------- helpers
    def term(self, computation, days=10):
        return self.env['account.payment.term'].create({
            'name': f'2/{days} net 30 ({computation})', 'early_discount': True, 'discount_percentage': 2.0,
            'discount_days': days, 'early_pay_discount_computation': computation,
            'line_ids': [Command.create({'value': 'percent', 'value_amount': 100.0, 'nb_days': 30})]})

    def inv(self, term, move_type='out_invoice', currency=None, date=D1, price=1000.0, partner=None):
        tax = self.sale_tax if move_type.startswith('out_') else self.purchase_tax
        move = self.env['account.move'].create({
            'move_type': move_type, 'partner_id': (partner or self.customer).id, 'invoice_date': date, 'date': date,
            'currency_id': (currency or self.aed).id, 'invoice_payment_term_id': term.id if term else False,
            'invoice_line_ids': [Command.create({'name': 'Service', 'quantity': 1, 'price_unit': price,
                                                 'tax_ids': [Command.set(tax.ids)]})]})
        move.action_post()
        return move

    def paid(self, inv, amount, date, journal=None, outbound=False):
        st = self.line(-amount if outbound else amount, journal=journal or self.misr, partner=inv.partner_id,
                       label='Early payment', date=date)
        self.match(st, self.open_item(inv))
        st.invalidate_recordset(['nc_early_discount_available'])
        return st

    def total(self, account, moves):
        amls = self.env['account.move.line'].search([('account_id', '=', account.id), ('move_id', 'in', moves.ids)])
        return round(sum(amls.mapped('balance')), 2)

    def settle(self, st, inv):
        """Apply the discount, validate, and prove the document is closed and the entry balanced."""
        self.assertTrue(st.nc_early_discount_available)
        st.action_nc_apply_early_discount()
        self.assertTrue(st.reconcile_data_info['can_reconcile'])
        st.reconcile_bank_line()
        self.assertTrue(st.is_reconciled)
        self.assert_balanced(st.move_id)
        self.assertEqual((inv.amount_residual, inv.amount_residual_signed), (0.0, 0.0))
        self.assertIn(inv.payment_state, ('paid', 'in_payment'))

    # ---------------------------------------------------------------- modes
    def test_01_mixed_mode_aed_with_vat(self):
        """'mixed' (Odoo: the tax is reduced on the invoice itself, the base discount is taken on payment).
        Hand: base 1000, tax on the discounted base 5% x 980 = 49, invoice 1,049; paying early deducts the 2% of
        the base = 20 -> bank 1,029."""
        inv = self.inv(self.term('mixed'))
        self.assertAlmostEqual(inv.amount_total, 1049.0)
        st = self.paid(inv, 1029.0, D1 + timedelta(days=5))
        self.settle(st, inv)
        self.assertAlmostEqual(self.total(self.discount_loss, st.move_id), 20.0)
        self.assertAlmostEqual(self.total(self.journal_bank_account(self.misr), st.move_id), 1029.0)
        self.assertFalse(st.move_id.line_ids.filtered(lambda ml: ml.tax_line_id), 'mixed: no tax correction at payment')

    def test_02_included_and_excluded_hand_figures(self):
        """included: 2 % off the base AND the tax -> bank 1,029 (base 20, tax 1). excluded: 2 % of the base only -> 1,030."""
        inc = self.inv(self.term('included'))
        self.assertAlmostEqual(inc.amount_total, 1050.0)
        st = self.paid(inc, 1029.0, D1 + timedelta(days=5))
        self.settle(st, inc)
        self.assertAlmostEqual(self.total(self.discount_loss, st.move_id), 20.0)
        self.assertAlmostEqual(sum(st.move_id.line_ids.filtered(lambda ml: ml.tax_line_id == self.sale_tax).mapped('balance')), 1.0)
        exc = self.inv(self.term('excluded'))
        st2 = self.paid(exc, 1030.0, D1 + timedelta(days=5))
        self.settle(st2, exc)
        self.assertAlmostEqual(self.total(self.discount_loss, st2.move_id), 20.0)
        self.assertFalse(st2.move_id.line_ids.filtered(lambda ml: ml.tax_line_id))

    # ------------------------------------------------------------- the window
    def test_03_window_boundaries(self):
        inv = self.inv(self.term('included', days=10))             # discount date = D1 + 10
        on_the_day = self.paid(inv, 1029.0, D1 + timedelta(days=10))
        self.assertTrue(on_the_day.nc_early_discount_available, 'the last day is inside the window')
        next_day = self.paid(inv, 1029.0, D1 + timedelta(days=11))
        self.assertFalse(next_day.nc_early_discount_available, 'one day later is outside')
        with self.assertRaises(UserError):
            next_day.action_nc_apply_early_discount()
        self.assertFalse(next_day.is_reconciled)
        self.assertAlmostEqual(inv.amount_residual, 1050.0, msg='nothing was booked by the refused attempt')

    # ----------------------------------------------------------- amounts
    def test_04_partial_payment_gets_no_discount_and_stays_partial(self):
        inv = self.inv(self.term('included'))
        st = self.paid(inv, 500.0, D1 + timedelta(days=3))
        self.assertFalse(st.nc_early_discount_available, 'a partial payment is not the discounted total')
        st.reconcile_bank_line()
        self.assertAlmostEqual(inv.amount_residual, 550.0)
        self.assertEqual(inv.payment_state, 'partial')
        later = self.line(1029.0, partner=self.customer, label='Rest', date=D1 + timedelta(days=4))
        self.match(later, self.open_item(inv))
        later.invalidate_recordset(['nc_early_discount_available'])
        self.assertFalse(later.nc_early_discount_available, 'a part-paid invoice is never discounted afterwards')

    def test_05_wrong_amount_is_never_discounted(self):
        inv = self.inv(self.term('included'))
        for amount in (1028.99, 1029.01, 1050.0):
            st = self.paid(inv, amount, D1 + timedelta(days=2))
            self.assertFalse(st.nc_early_discount_available, f'{amount}')

    # ------------------------------------------------------------ credit notes
    def test_06_credit_note_gets_no_discount_and_is_refunded_normally(self):
        term = self.term('included')
        credit = self.inv(term, move_type='out_refund', price=500.0)
        self.assertFalse(credit._is_eligible_for_early_payment_discount(self.aed, D1))
        st = self.line(-525.0, partner=self.customer, label='Refund to customer', date=D1 + timedelta(days=2))
        self.match(st, self.open_item(credit))
        st.invalidate_recordset(['nc_early_discount_available'])
        self.assertFalse(st.nc_early_discount_available)
        st.reconcile_bank_line()
        self.assert_balanced(st.move_id)
        self.assertEqual(credit.amount_residual, 0.0)
        self.assertEqual(self.total(self.discount_loss, st.move_id), 0.0)

    # ------------------------------------------------------------ vendor bills
    def test_07_vendor_bill_discount_is_a_gain(self):
        bill = self.inv(self.term('included'), move_type='in_invoice', partner=self.vendor)
        st = self.paid(bill, 1029.0, D1 + timedelta(days=4), outbound=True)
        self.settle(st, bill)
        self.assertAlmostEqual(self.total(self.discount_gain, st.move_id), -20.0, msg='we pay 20 less: a discount gain')
        self.assertAlmostEqual(self.total(self.journal_bank_account(self.misr), st.move_id), -1029.0)

    # --------------------------------------------------------- foreign currency
    def test_08_eur_invoice_paid_at_another_rate(self):
        """EUR invoice on D1 (1 EUR = 4.00 AED): base 100 EUR, VAT 5 EUR, total 105 EUR = 420.00 AED. Included, 2 %:
        the customer pays 102.90 EUR on D2 (1 EUR = 4.40 AED) = 452.76 AED. Discount booked at the invoice's own rate:
        base 2.00 EUR = 8.00 AED, tax 0.10 EUR = 0.40 AED. The bank receives 452.76 AED for what the ledger carried as
        420.00 - 8.40 = 411.60 AED, so the exchange gain is 41.16 AED (= 102.90 x 0.40), booked once."""
        inv = self.inv(self.term('included', days=30), currency=self.eur, price=100.0)
        self.assertAlmostEqual(inv.amount_total, 105.0)
        self.assertAlmostEqual(inv.amount_total_signed, 420.0)
        st = self.paid(inv, 102.9, D2, journal=self.eur_bank)
        self.settle(st, inv)
        moves = st.move_id | self.env['account.move'].search([('id', 'in', (inv.line_ids.matched_debit_ids | inv.line_ids.matched_credit_ids).mapped('exchange_move_id').ids)])
        self.assertAlmostEqual(self.total(self.discount_loss, moves), 8.0)
        self.assertAlmostEqual(sum(moves.line_ids.filtered(lambda ml: ml.tax_line_id == self.sale_tax).mapped('balance')), 0.4)
        self.assertAlmostEqual(-self.total(self.gain, moves), 41.16)
        self.assertAlmostEqual(self.total(self.loss, moves), 0.0)
        self.assertAlmostEqual(self.total(self.journal_bank_account(self.eur_bank), st.move_id), 452.76)
        self.assertEqual(inv.amount_residual, 0.0)

    def test_09_eur_invoice_paid_at_the_same_rate_has_no_exchange_entry(self):
        inv = self.inv(self.term('included', days=30), currency=self.eur, price=100.0)
        st = self.paid(inv, 102.9, D1 + timedelta(days=2), journal=self.eur_bank)
        self.settle(st, inv)
        exchange = (inv.line_ids.matched_debit_ids | inv.line_ids.matched_credit_ids).mapped('exchange_move_id')
        self.assertFalse(exchange, 'same rate on both dates: no exchange entry')
        self.assertAlmostEqual(self.total(self.discount_loss, st.move_id), 8.0)

    def test_10_eur_mixed_mode_at_another_rate(self):
        """Mixed, EUR: base 100, tax 5 % of 98 = 4.90, invoice 104.90 EUR = 419.60 AED. Early discount 2.00 EUR = 8.00 AED
        -> 102.90 EUR due. Received on D2: 452.76 AED vs ledger 419.60 - 8.00 = 411.60 -> exchange gain 41.16."""
        inv = self.inv(self.term('mixed', days=30), currency=self.eur, price=100.0)
        self.assertAlmostEqual(inv.amount_total, 104.9)
        st = self.paid(inv, 102.9, D2, journal=self.eur_bank)
        self.settle(st, inv)
        moves = st.move_id | (inv.line_ids.matched_debit_ids | inv.line_ids.matched_credit_ids).mapped('exchange_move_id')
        self.assertAlmostEqual(self.total(self.discount_loss, moves), 8.0)
        self.assertAlmostEqual(-self.total(self.gain, moves), 41.16)

    def test_11_foreign_discount_outside_the_window_is_refused(self):
        inv = self.inv(self.term('included', days=10), currency=self.eur, price=100.0)
        st = self.paid(inv, 102.9, D2, journal=self.eur_bank)               # D2 is 19 days after D1
        self.assertFalse(st.nc_early_discount_available)
        with self.assertRaises(UserError):
            st.action_nc_apply_early_discount()

    def test_12_a_transaction_in_another_currency_than_the_invoice_gets_no_discount(self):
        inv = self.inv(self.term('included', days=30), currency=self.eur, price=100.0)
        st = self.paid(inv, 420.0, D1 + timedelta(days=2))                    # AED bank line for a EUR invoice
        self.assertFalse(st.nc_early_discount_available)

    # --------------------------------------------------- undo and re-reconcile
    def test_13_undo_and_reconcile_again_books_the_discount_once(self):
        inv = self.inv(self.term('included'))
        st = self.paid(inv, 1029.0, D1 + timedelta(days=5))
        self.settle(st, inv)
        st.unreconcile_bank_line()
        self.assertAlmostEqual(inv.amount_residual, 1050.0)
        self.assertEqual(inv.payment_state, 'not_paid')
        # the discount lines are not left behind in the ledger once the reconciliation is undone
        live = self.env['account.move.line'].search([('account_id', '=', self.discount_loss.id), ('move_id', '=', st.move_id.id)])
        self.assertAlmostEqual(sum(live.mapped('balance')), 0.0)
        again = self.line(1029.0, partner=self.customer, label='Early payment again', date=D1 + timedelta(days=6))
        self.match(again, self.open_item(inv))
        again.invalidate_recordset(['nc_early_discount_available'])
        self.settle(again, inv)
        self.assertAlmostEqual(self.total(self.discount_loss, again.move_id), 20.0)

    def test_14_ambiguous_candidates_propose_nothing(self):
        term = self.term('included')
        self.inv(term)
        self.inv(term)
        st = self.line(1029.0, partner=self.customer, label='Customer transfer', date=D1 + timedelta(days=3))
        st.invalidate_recordset()
        self.assertFalse([d for d in st.reconcile_data_info['data'] if d.get('kind') == 'other'])
        self.assertFalse(st.is_reconciled)

    # ----------------------------------------------------------------- misc
    def journal_bank_account(self, journal):
        return journal.default_account_id
