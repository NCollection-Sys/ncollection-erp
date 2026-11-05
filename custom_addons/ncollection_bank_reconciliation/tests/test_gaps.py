# flake8: noqa: E501  (fixture files and accounting assertions read best on one line)
"""The gap closure: OFX import, payment batches, early-payment discounts, duplicate review, Arabic OCA messages."""
import base64
from datetime import timedelta

from odoo import Command
from odoo.exceptions import UserError, ValidationError
from odoo.tests import Form, tagged
from odoo.tools.translate import code_translations

from .common import VIEW, BankCommon

OFX = """OFXHEADER:100
DATA:OFXSGML
VERSION:102
ENCODING:USASCII

<OFX>
<BANKMSGSRSV1><STMTTRNRS><TRNUID>1<STMTRS>
<CURDEF>%(ccy)s
<BANKACCTFROM><BANKID>MISR<ACCTID>%(acct)s<ACCTTYPE>CHECKING</BANKACCTFROM>
<BANKTRANLIST><DTSTART>20260301<DTEND>20260331
<STMTTRN><TRNTYPE>CREDIT<DTPOSTED>20260305120000<TRNAMT>1500.00<FITID>MISR-0001<NAME>Nile Customer<MEMO>Invoice payment</STMTTRN>
<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20260306<TRNAMT>-35.50<FITID>MISR-0002<NAME>Bank charges</STMTTRN>
<STMTTRN><TRNTYPE>CREDIT<DTPOSTED>20260306<TRNAMT>1500.00<FITID>MISR-0003<NAME>Nile Customer<MEMO>Invoice payment</STMTTRN>
</BANKTRANLIST>
<LEDGERBAL><BALAMT>12964.50<DTASOF>20260331</LEDGERBAL>
</STMTRS></STMTTRNRS></BANKMSGSRSV1>
</OFX>
"""


@tagged('post_install', '-at_install', 'ncollection_bank_reconciliation')
class TestGapClosure(BankCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.misr.bank_account_id = cls.env['res.partner.bank'].create(
            {'acc_number': 'EG380019000500000000263180002', 'partner_id': cls.company.partner_id.id})
        cls.ccy = (cls.misr.currency_id or cls.company.currency_id).name

    def ofx(self, **kw):
        values = {'ccy': self.ccy, 'acct': 'EG380019000500000000263180002'}
        values.update(kw)
        return (OFX % values).encode()

    def upload(self, content, name='march.ofx', journal=None):
        ctx = (journal or self.misr).action_nc_bank_upload()['context']
        wiz = self.env['account.statement.import'].with_context(**ctx).create(
            {'statement_file': base64.b64encode(content), 'statement_filename': name})
        wiz.action_nc_preview()
        return wiz

    def lines(self, journal=None):
        return self.env['account.bank.statement.line'].search([('journal_id', '=', (journal or self.misr).id)])

    # ------------------------------------------------------------------ OFX
    def test_ofx_import_preview_and_bank_id_dedup(self):
        wiz = self.upload(self.ofx())
        self.assertEqual(wiz.nc_preview_count, 3)
        self.assertIn('3 transactions read', wiz.nc_preview_html)
        wiz.import_file_button()
        lines = self.lines()
        self.assertEqual(sorted(lines.mapped('amount')), [-35.5, 1500.0, 1500.0], 'two equal receipts, two FITIDs: both kept')
        self.assertTrue(all('MISR-000' in uid for uid in lines.mapped('unique_import_id')), "the bank's FITID is the key")
        statement = lines.statement_id
        self.assertEqual(statement.balance_end_real, 12964.5)
        self.assertEqual(round(statement.balance_start, 2), 10000.0)
        again = self.upload(self.ofx(), 'march-again.ofx')
        self.assertEqual(again.nc_preview_count, 0)
        self.assertEqual(len(self.lines()), 3)

    def test_ofx_malformed_or_foreign_files_are_refused_whole(self):
        bad = self.ofx().replace(b'<TRNAMT>-35.50', b'<TRNAMT>35,5x')
        with self.assertRaises(UserError):
            self.upload(bad)
        with self.assertRaises(UserError):
            self.upload(self.ofx(acct='9999999'))
        with self.assertRaises(UserError):
            self.upload(self.ofx(ccy='JPY' if self.ccy != 'JPY' else 'EUR'))
        with self.assertRaises(UserError):
            self.upload(b'0000005112025HSBC BELGIUM CODA FILE', 'statement.cod')    # unsupported format: refused, named
        self.assertFalse(self.lines(), 'nothing imported by any refused file')

    # ---------------------------------------------------------------- batch
    def _payment(self, amount, partner=None):
        pay = self.env['account.payment'].create({
            'payment_type': 'inbound', 'partner_type': 'customer', 'partner_id': (partner or self.customer).id,
            'amount': amount, 'journal_id': self.misr.id, 'date': self.day})
        pay.action_post()
        return pay

    def test_batch_matching_and_no_double_use(self):
        p1, p2 = self._payment(1000.0), self._payment(500.0)
        batch = self.env['ncollection.bank.payment.batch'].create(
            {'journal_id': self.misr.id, 'payment_type': 'inbound', 'payment_ids': [Command.set((p1 | p2).ids)]})
        self.assertEqual((batch.amount, batch.state), (1500.0, 'open'))
        items = batch._nc_outstanding_items()
        if not items:
            self.skipTest('this chart posts payments straight to the bank account: nothing to match')
        st = self.line(1500.0, label='Deposit slip 42')
        wizard = self.env['ncollection.bank.batch.match'].create({'statement_line_id': st.id, 'batch_id': batch.id})
        wizard.action_match()
        self.assertTrue(st.reconcile_data_info['can_reconcile'])
        st.reconcile_bank_line()
        self.assertTrue(st.is_reconciled)
        self.assert_balanced(st.move_id)
        self.assertTrue(all(items.mapped('reconciled')))
        batch.invalidate_recordset(['state'])
        self.assertEqual(batch.state, 'matched')
        other = self.line(1500.0, label='Same slip again')
        with self.assertRaises(UserError):
            other.action_nc_match_batch(batch)          # nothing left to match: never twice
        p3 = self._payment(70.0)
        open_batch = self.env['ncollection.bank.payment.batch'].create(
            {'journal_id': self.misr.id, 'payment_type': 'inbound', 'payment_ids': [Command.set(p3.ids)]})
        with self.assertRaises(ValidationError):
            self.env['ncollection.bank.payment.batch'].create(
                {'journal_id': self.misr.id, 'payment_type': 'inbound', 'payment_ids': [Command.set(p3.ids)]})
        self.assertEqual(open_batch.payment_count, 1)

    def test_batch_refuses_other_journal_or_direction(self):
        pay = self._payment(10.0)
        with self.assertRaises(ValidationError):
            self.env['ncollection.bank.payment.batch'].create(
                {'journal_id': self.ahly.id, 'payment_type': 'inbound', 'payment_ids': [Command.set(pay.ids)]})
        with self.assertRaises(ValidationError):
            self.env['ncollection.bank.payment.batch'].create(
                {'journal_id': self.misr.id, 'payment_type': 'outbound', 'payment_ids': [Command.set(pay.ids)]})

    # ------------------------------------------------------- early discount
    def _discount_term(self, computation):
        return self.env['account.payment.term'].create({
            'name': '2/10 net 30 (%s)' % computation, 'early_discount': True, 'discount_percentage': 2.0,
            'discount_days': 10, 'early_pay_discount_computation': computation,
            'line_ids': [Command.create({'value': 'percent', 'value_amount': 100.0, 'nb_days': 30})]})

    def _taxed_invoice(self, term):
        tax = self.env['account.tax'].search([('name', '=', 'VAT 5% (test)'), ('company_id', '=', self.company.id)], limit=1)
        tax = tax or self.env['account.tax'].create({'name': 'VAT 5% (test)', 'amount': 5.0, 'type_tax_use': 'sale'})
        move = self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': self.customer.id, 'invoice_date': self.day, 'date': self.day,
            'invoice_payment_term_id': term.id,
            'invoice_line_ids': [Command.create({'name': 'Service', 'quantity': 1, 'price_unit': 1000.0,
                                                 'tax_ids': [Command.set(tax.ids)]})]})
        move.action_post()
        return move, tax

    def _discount_flow(self, computation, expected_paid):
        inv, tax = self._taxed_invoice(self._discount_term(computation))
        self.assertEqual(inv.amount_total, 1050.0)
        st = self.line(expected_paid, partner=self.customer, label='Early payment', date=self.day + timedelta(days=5))
        self.match(st, self.open_item(inv))
        st.invalidate_recordset(['nc_early_discount_available'])
        self.assertTrue(st.nc_early_discount_available, 'inside the window, exact discounted total')
        st.action_nc_apply_early_discount()
        self.assertTrue(st.reconcile_data_info['can_reconcile'])
        st.reconcile_bank_line()
        self.assertTrue(st.is_reconciled)
        self.assert_balanced(st.move_id)
        self.assertEqual(inv.amount_residual, 0.0)
        self.assertIn(inv.payment_state, ('paid', 'in_payment'))
        return inv, tax, st

    def test_early_discount_tax_included(self):
        """'included': the discount reduces the tax base, so 2% comes off both the 1,000 base and the 50 tax."""
        _inv, tax, st = self._discount_flow('included', 1029.0)
        tax_lines = st.move_id.line_ids.filtered(lambda ml: ml.tax_line_id == tax)
        self.assertAlmostEqual(sum(tax_lines.mapped('balance')), 1.0, msg='tax reversed on the discount: 2% of 50')
        base = st.move_id.line_ids.filtered(lambda ml: tax in ml.tax_ids)
        self.assertAlmostEqual(sum(base.mapped('balance')), 20.0, msg='discount on the base: 2% of 1,000')

    def test_early_discount_tax_excluded(self):
        """'excluded': the discount is on the untaxed amount only (2% of 1,000 = 20), the tax is unchanged."""
        _inv, tax, st = self._discount_flow('excluded', 1030.0)
        self.assertFalse(st.move_id.line_ids.filtered(lambda ml: ml.tax_line_id == tax), 'no tax correction')

    def test_early_discount_offered_on_the_first_opening(self):
        """Regression (found in the browser): on a transaction never opened before, the engine's auto-match stores its
        proposal while the workspace loads; the discount must already be offered on that first load (web_read), not only
        on a later visit."""
        inv, _tax = self._taxed_invoice(self._discount_term('included'))
        st = self.line(1029.0, partner=self.customer, label='Customer transfer', date=self.day + timedelta(days=3))
        self.assertFalse(st.reconcile_data, 'never opened: nothing stored yet')
        st.invalidate_recordset()
        result = st.web_read({'nc_early_discount_available': {}, 'reconcile_data_info': {}})
        proposed = [d.get('id') for d in result[0]['reconcile_data_info']['data'] if d.get('kind') == 'other']
        self.assertEqual(proposed, self.open_item(inv).ids, 'the DISCOUNTED amount proposes the invoice (no label match)')
        self.assertTrue(result[0]['nc_early_discount_available'], 'and the discount is offered on that same load')

    def test_discounted_match_is_never_an_ambiguous_guess(self):
        term = self._discount_term('included')
        self._taxed_invoice(term)
        self._taxed_invoice(term)                       # two invoices with the same discounted total
        st = self.line(1029.0, partner=self.customer, label='Customer transfer', date=self.day + timedelta(days=3))
        st.invalidate_recordset()
        proposed = [d for d in st.reconcile_data_info['data'] if d.get('kind') == 'other']
        self.assertFalse(proposed, 'two equal candidates: nothing proposed')
        self.assertFalse(st.is_reconciled, 'and nothing posted')

    def test_no_discount_outside_the_window_or_for_another_amount(self):
        inv, _tax = self._taxed_invoice(self._discount_term('included'))
        late = self.line(1029.0, partner=self.customer, label='Late', date=self.day + timedelta(days=20))
        self.match(late, self.open_item(inv))
        late.invalidate_recordset(['nc_early_discount_available'])
        self.assertFalse(late.nc_early_discount_available, 'after the discount date')
        with self.assertRaises(UserError):
            late.action_nc_apply_early_discount()
        wrong = self.line(1000.0, partner=self.customer, label='Wrong amount', date=self.day + timedelta(days=2))
        self.match(wrong, self.open_item(inv))
        wrong.invalidate_recordset(['nc_early_discount_available'])
        self.assertFalse(wrong.nc_early_discount_available, 'not the discounted total: never an invented discount')

    # ------------------------------------------------------ duplicate review
    def test_duplicate_review_explains_and_never_deletes(self):
        a = self.line(250.0, label='Cash deposit')
        b = self.line(250.0, label='cash  DEPOSIT')
        self.line(250.0, label='Cash deposit', date=self.day + timedelta(days=1))   # another day: not a candidate
        c = self.line(80.0, label='Card', unique_import_id='BANK-AAA')
        self.line(80.0, label='Card', unique_import_id='BANK-BBB')                   # two bank ids: not a duplicate
        action = self.misr.action_nc_bank_duplicates()
        review = self.env['ncollection.bank.duplicate.review'].browse(action['res_id'])
        pairs = {(cand.line_a_id, cand.line_b_id) for cand in review.candidate_ids}
        self.assertEqual(pairs, {(a, b)}, 'only the real candidate')
        self.assertIn('same label', review.candidate_ids.reason)
        self.assertIn('no bank transaction id', review.candidate_ids.reason)
        self.assertNotIn(c, review.candidate_ids.line_a_id)
        count = self.env['account.bank.statement.line'].search_count([])
        review.candidate_ids.action_not_duplicate()
        self.assertTrue(a.nc_duplicate_reviewed and b.nc_duplicate_reviewed)
        self.assertIn('not a duplicate', ' '.join(a.move_id.message_ids.mapped('body')))
        self.assertEqual(self.env['account.bank.statement.line'].search_count([]), count, 'nothing deleted')
        again = self.env['ncollection.bank.duplicate.review'].browse(self.misr.action_nc_bank_duplicates()['res_id'])
        self.assertFalse(again.candidate_ids)

    # -------------------------------------------------------- Arabic messages
    def test_oca_python_messages_have_arabic(self):
        code_translations.python_translations.pop(('account_statement_import_file', 'ar_001'), None)
        table = code_translations.get_python_translations('account_statement_import_file', 'ar_001')
        self.assertEqual(table.get('Missing currency code in the bank statement file.'), 'رمز العملة مفقود في ملف كشف البنك.')
        code_translations.python_translations.pop(('account_reconcile_oca', 'ar_001'), None)
        self.assertIn('You can only reconcile journal items belonging to the same account.',
                      code_translations.get_python_translations('account_reconcile_oca', 'ar_001'))
        # inert for any other module
        code_translations.python_translations.pop(('base', 'ar_001'), None)
        self.assertNotIn('Missing currency code in the bank statement file.',
                         code_translations.get_python_translations('base', 'ar_001'))
