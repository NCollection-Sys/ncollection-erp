# -*- coding: utf-8 -*-
"""The invoice's Print Settings: the optional payment-status section and the paper, the same for Preview and Print,
and the accounting documents' settings changeable only by the Accounting Adviser."""
import html as htmllib
from datetime import date

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import AccessError
from odoo.tests import new_test_user, tagged


@tagged('post_install', '-at_install')
class TestInvoicePrintSettings(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.kind = cls.env.ref('ncollection_account_core.doc_type_customer_invoice')
        cls.bill_kind = cls.env.ref('ncollection_account_core.doc_type_vendor_bill')

    def _invoice(self):
        move = self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': self.partner_a.id, 'invoice_date': date(2026, 3, 1),
            'invoice_line_ids': [(0, 0, {'name': 'Line', 'quantity': 2, 'price_unit': 150.0})]})
        move.action_post()
        return move

    def _html(self, move):
        report = self.env.ref('account.account_invoices').with_context(force_report_rendering=True)
        out = report._render_qweb_html(report.report_name, [move.id])[0]
        return htmllib.unescape(out.decode() if isinstance(out, bytes) else out)

    def test_the_payment_status_section_follows_the_print_setting(self):
        move = self._invoice()
        self.assertIn('o_nc_payment_status', self._html(move))
        self.kind.nc_show_payment_status = False
        self.assertFalse(move._nc_print_shows_payment_status())
        page = self._html(move)
        self.assertNotIn('o_nc_payment_status', page)
        self.assertIn(move.name, page, 'only the optional section goes, never the document')
        self.assertEqual(move.amount_total, 300.0, 'presentation only: no figure changes')

    def test_view_and_print_use_the_same_paper(self):
        move = self._invoice()
        letter = self.env['report.paperformat'].create({'name': 'Letter (test)', 'format': 'Letter'})
        self.kind.paperformat_id = letter
        report = move._nc_print_report()
        self.assertEqual(report.get_paperformat(), letter)
        self.assertEqual(self.bill_kind.paperformat_id, letter, 'one invoice report, one paper')

    def test_the_accounting_documents_are_changed_by_the_accounting_adviser_only(self):
        adviser = new_test_user(self.env, login='print.adviser', groups='account.group_account_manager')
        accountant = new_test_user(self.env, login='print.accountant', groups='account.group_account_user')
        self.kind.with_user(adviser).write({'nc_show_payment_status': False})
        self.assertFalse(self.kind.nc_show_payment_status)
        with self.assertRaises(AccessError):
            self.kind.with_user(accountant).write({'nc_show_payment_status': True})
        with self.assertRaises(AccessError):
            self.kind.with_user(adviser).write({'name': 'Renamed'})

    # ------------------------------------------------------------------ Print Templates (ncollection_branding)
    def _nc_layout(self):
        self.env.company.external_report_layout_id = self.env.ref('ncollection_branding.external_layout_nc')

    def test_the_invoice_prints_its_print_template(self):
        self._nc_layout()
        move = self._invoice()
        page = self._html(move)
        self.kind.write({'nc_title': 'Tax Invoice Template', 'nc_primary_color': '#AA3300',
                         'nc_show_unit_price': False,
                         'nc_footer_text': 'Thank you for your business', 'nc_page_numbers': False,
                         'nc_show_payment_reference': False, 'nc_font': 'serif'})
        page = self._html(move)
        self.assertIn('Tax Invoice Template %s' % move.name, page)
        self.assertIn('#AA3300', page)
        self.assertIn('[name="th_priceunit"] { display: none; }', page)
        self.assertIn('[name^="payment_communication"] { display: none; }', page)
        self.assertIn('Thank you for your business', page)
        self.assertIn('DejaVu Serif', page)
        self.assertFalse(self.env['ncollection.document.type']._nc_print_options(move)['page_numbers'])
        self.assertEqual(move.amount_total, 300.0, 'presentation only: no figure changes')

    def test_a_bill_keeps_its_own_template(self):
        self._nc_layout()
        self.kind.nc_title = 'Customer Only Title'
        bill = self.env['account.move'].create({
            'move_type': 'in_invoice', 'partner_id': self.partner_a.id, 'invoice_date': date(2026, 3, 1),
            'invoice_line_ids': [(0, 0, {'name': 'Line', 'quantity': 1, 'price_unit': 10.0})]})
        bill.action_post()
        self.assertNotIn('Customer Only Title', self._html(bill))

    def test_the_page_settings_become_the_paper_print_and_preview_use(self):
        self.kind.write({'nc_paper_size': 'A5', 'nc_orientation': 'Landscape', 'nc_margin_top': 30})
        paper = self.kind.paperformat_id
        self.assertEqual((paper.format, paper.orientation, paper.margin_top), ('A5', 'Landscape', 30))
        move = self._invoice()
        self.assertEqual(move._nc_print_report().get_paperformat(), paper)
        self.kind.nc_margin_top = 40
        self.assertEqual(self.kind.paperformat_id, paper, 'the same paper, kept in step')
        self.assertEqual(paper.margin_top, 40)
        action = self.kind.action_nc_preview()
        self.assertEqual(action['url'], '/report/pdf/account.report_invoice_with_payments/%s' % move.id,
                         'Preview is the report Print uses, on a real document of this kind')

    def test_template_values_are_validated(self):
        from odoo.exceptions import ValidationError
        for vals in ({'nc_primary_color': 'red;}body{display:none'}, {'nc_font_size': 40}, {'nc_margin_top': -1}):
            with self.subTest(vals=vals), self.assertRaises(ValidationError):
                self.kind.write(vals)

    def test_the_letterhead_prints_no_country_line(self):
        self._nc_layout()
        company = self.env.company
        company.partner_id.write({'country_id': self.env.ref('base.us').id, 'street': '1 Test Street'})
        company.company_details = False
        page = self._html(self._invoice())
        self.assertIn('1 Test Street', page)
        self.assertNotIn('United States', page, 'the configured country is never printed as a header line')
