# -*- coding: utf-8 -*-
"""Custom print title of one invoice: presentation only, default untouched, accounting untouched."""
import html as htmllib
from datetime import date

from odoo.addons.account.tests.common import AccountTestInvoicingHttpCommon
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import tagged
from odoo.tools import mute_logger


@tagged('post_install', '-at_install')
class TestInvoicePrintTitle(AccountTestInvoicingHttpCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company_data_2 = cls.setup_other_company()

    def _invoice(self, lines=1, move_type='out_invoice', post=True, **extra):
        move = self.env['account.move'].create(dict({
            'move_type': move_type, 'partner_id': self.partner_a.id, 'invoice_date': date(2026, 3, 1),
            'invoice_line_ids': [(0, 0, {'name': 'Line %d' % i, 'quantity': 1, 'price_unit': 100.0 + i})
                                 for i in range(lines)]}, **extra))
        if post:
            move.action_post()
        return move

    def _html(self, move, lang=None):
        report = self.env.ref('account.account_invoices').with_context(
            force_report_rendering=True, **({'lang': lang} if lang else {}))
        out = report._render_qweb_html(report.report_name, [move.id])[0]
        return htmllib.unescape(out.decode() if isinstance(out, bytes) else out)

    # ---------------------------------------------------------------- default
    def test_default_title_is_the_stock_heading(self):
        move = self._invoice()
        self.assertFalse(move.nc_print_title_enabled)
        self.assertFalse(move._nc_document_title())
        page = self._html(move)
        self.assertIn(move.name, page)
        self.assertNotIn('o_nc_document_title', page)

    def test_enabled_but_empty_is_refused_and_disabled_with_text_is_ignored(self):
        move = self._invoice(post=False)
        with self.assertRaises(ValidationError):
            move.write({'nc_print_title_enabled': True, 'nc_print_title': '   '})
        move.write({'nc_print_title_enabled': False, 'nc_print_title': 'IGNORED'})
        self.assertFalse(move._nc_document_title())
        self.assertNotIn('IGNORED', self._html(move))

    # ---------------------------------------------------------------- custom
    def test_custom_english_title_prints_with_the_number(self):
        move = self._invoice()
        move.write({'nc_print_title_enabled': True, 'nc_print_title': 'TAX INVOICE'})
        page = self._html(move)
        self.assertIn('TAX INVOICE', page)
        self.assertIn(move.name, page)
        self.assertIn('o_nc_document_title', page)

    def test_custom_arabic_title_prints_in_both_directions(self):
        self.env['res.lang']._activate_lang('ar_001')
        move = self._invoice()
        move.write({'nc_print_title_enabled': True, 'nc_print_title': 'فاتورة ضريبية'})
        for lang in (None, 'ar_001'):
            self.assertIn('فاتورة ضريبية', self._html(move, lang), lang)

    def test_switching_it_off_restores_the_stock_heading(self):
        move = self._invoice()
        move.write({'nc_print_title_enabled': True, 'nc_print_title': 'TAX INVOICE'})
        move.write({'nc_print_title_enabled': False})
        self.assertNotIn('TAX INVOICE', self._html(move))
        self.assertFalse(move._nc_document_title())

    def test_the_title_belongs_to_one_document(self):
        first = self._invoice()
        first.write({'nc_print_title_enabled': True, 'nc_print_title': 'TAX INVOICE'})
        other = self._invoice()
        self.assertNotIn('TAX INVOICE', self._html(other))
        self.assertFalse(first.copy().nc_print_title_enabled)         # a duplicate starts from the stock heading

    def test_too_long_and_multiline_titles_are_refused(self):
        move = self._invoice(post=False)
        with self.assertRaises(ValidationError):
            move.write({'nc_print_title_enabled': True, 'nc_print_title': 'X' * 81})
        with self.assertRaises(ValidationError):
            move.write({'nc_print_title_enabled': True, 'nc_print_title': 'A\nB'})
        move.write({'nc_print_title_enabled': True, 'nc_print_title': 'X' * 80})        # the limit itself is fine
        self.assertIn('overflow-wrap', self._html(move))                               # and it wraps, not widens

    def test_credit_note_uses_it_too(self):
        invoice = self._invoice()
        credit = invoice._reverse_moves([{'invoice_date': date(2026, 3, 2)}])
        credit.write({'nc_print_title_enabled': True, 'nc_print_title': 'إشعار دائن'})
        self.assertEqual(credit.move_type, 'out_refund')
        self.assertIn('إشعار دائن', self._html(credit))

    def test_multi_page_invoice_renders_with_the_title(self):
        move = self._invoice(lines=90)
        move.write({'nc_print_title_enabled': True, 'nc_print_title': 'TAX INVOICE'})
        page = self._html(move)
        self.assertEqual(page.count('TAX INVOICE'), 1)
        self.assertIn('Line 89', page)
        report = self.env.ref('account.account_invoices').with_context(force_report_rendering=True)
        pdf = report._render_qweb_pdf(report.report_name, [move.id])[0]
        self.assertTrue(pdf.startswith(b'%PDF'))

    # ---------------------------------------------------------------- accounting untouched
    def test_accounting_data_number_state_and_posting_are_unchanged(self):
        move = self._invoice(post=False)
        before = (move.name, move.state, move.amount_total, move.partner_id.id, move.invoice_date)
        move.write({'nc_print_title_enabled': True, 'nc_print_title': 'TAX INVOICE'})
        self.assertEqual(before, (move.name, move.state, move.amount_total, move.partner_id.id, move.invoice_date))
        move.action_post()
        snapshot = [(line.account_id.id, line.debit, line.credit) for line in move.line_ids.sorted('id')]
        self.assertEqual(move.state, 'posted')
        self.assertNotEqual(move.name, '/')
        # editing the title of a POSTED invoice changes nothing in the books
        name = move.name
        move.write({'nc_print_title': 'فاتورة بيع'})
        self.assertEqual((move.name, move.state), (name, 'posted'))
        after = [(line.account_id.id, line.debit, line.credit) for line in move.line_ids.sorted('id')]
        self.assertEqual(snapshot, after)

    def test_the_stock_heading_is_not_edited_for_other_moves(self):
        bill = self._invoice(move_type='in_invoice')
        self.assertFalse(bill._nc_document_title())

    # ---------------------------------------------------------------- isolation
    def test_company_isolation(self):
        company_b = self.company_data_2['company']
        move_b = self.env['account.move'].with_company(company_b).create({
            'move_type': 'out_invoice', 'partner_id': self.partner_a.id, 'invoice_date': date(2026, 3, 1),
            'journal_id': self.company_data_2['default_journal_sale'].id,
            'invoice_line_ids': [(0, 0, {'name': 'B line', 'quantity': 1, 'price_unit': 10.0})]})
        user_a = self.env['res.users'].create({
            'name': 'Accountant A', 'login': 'nc_title_a', 'company_id': self.env.company.id,
            'company_ids': [(6, 0, self.env.company.ids)],
            'group_ids': [(6, 0, [self.env.ref('account.group_account_invoice').id])]})
        with self.assertRaises(AccessError), mute_logger('odoo.addons.base.models.ir_rule'):
            move_b.with_user(user_a).write({'nc_print_title_enabled': True, 'nc_print_title': 'LEAK'})
        self.assertFalse(move_b.nc_print_title_enabled)
