# -*- coding: utf-8 -*-
"""Request 06: Print Settings belong to ONE invoice: they reach its PDF and nothing else (not the company, not the
template, not another invoice); payment history shows / hides Odoo's own 'Paid on' lines; access follows the
document."""
from odoo import Command, fields
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import new_test_user, tagged


@tagged('post_install', '-at_install')
class TestMovePrintSettings(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.DocType = cls.env['ncollection.document.type']
        cls.inv_a = cls._nc_invoice(100.0)
        cls.inv_b = cls._nc_invoice(200.0)

    @classmethod
    def _nc_invoice(cls, amount):
        move = cls.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': cls.partner_a.id, 'invoice_date': fields.Date.today(),
            'invoice_line_ids': [Command.create({'name': 'Goods', 'quantity': 1, 'price_unit': amount,
                                                 'tax_ids': [Command.clear()]})]})
        move.action_post()
        return move

    def _setting(self, move, **vals):
        return self.env['ncollection.move.print.setting'].create({'move_id': move.id, **vals})

    def _html(self, move):
        report = move._nc_print_report()
        html, _ = self.env['ir.actions.report'].with_context(**move._nc_print_context())._render_qweb_html(
            report.report_name, move.ids)
        return html.decode()

    def test_one_invoice_s_settings_never_reach_another_or_the_company(self):
        company_color = self.env.company.primary_color
        kind = self.DocType._nc_kind_of(self.inv_a)
        template_before = kind.read(kind._nc_print_setting_fields() - {'nc_logo'})[0] if kind else {}
        self._setting(self.inv_a, title='Tax Invoice (copy)', primary_color='#123456', show_quantity='hide',
                      paper_size='A5', orientation='Landscape')
        a = self.DocType._nc_print_options(self.inv_a)
        b = self.DocType._nc_print_options(self.inv_b)
        self.assertEqual((a['title'], a['accent']), ('Tax Invoice (copy)', '#123456'))
        self.assertIn('td_quantity', a['hidden_cells'])
        self.assertNotEqual(b['title'], 'Tax Invoice (copy)')
        self.assertNotIn('td_quantity', b['hidden_cells'])
        self.assertEqual(self.env.company.primary_color, company_color)
        if kind:
            self.assertEqual(kind.read(kind._nc_print_setting_fields() - {'nc_logo'})[0], template_before)
        paper = self.env['ir.actions.report'].with_context(**self.inv_a._nc_print_context()).get_paperformat()
        self.assertEqual((paper.format, paper.orientation), ('A5', 'Landscape'))
        self.assertNotIn('nc_print_paperformat_id', self.inv_b._nc_print_context())

    def test_payment_history_is_odoo_s_own_and_only_shown_or_hidden(self):
        self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=self.inv_a.ids).create({'amount': 40.0})._create_payments()
        state = self.inv_a.payment_state
        self._setting(self.inv_a, show_payment_history='show')
        self.assertIn('Paid on', self._html(self.inv_a))
        self.inv_a._nc_print_setting().show_payment_history = 'hide'
        self.assertNotIn('Paid on', self._html(self.inv_a))
        self.assertEqual(self.inv_a.payment_state, state, 'presentation only')
        self.assertAlmostEqual(self.inv_a.amount_residual, 60.0)

    def test_bank_terms_notes_are_per_document_switches(self):
        bank = self.env['res.partner.bank'].create({
            'acc_number': 'AE070331234567890123456', 'partner_id': self.env.company.partner_id.id,
            'allow_out_payment': True})
        term = self.env['account.payment.term'].create({'name': 'NC 45 days', 'note': '<p>NCTERMS pay in 45</p>'})
        moves = self._nc_invoice(300.0) | self._nc_invoice(400.0)
        for move in moves:
            move.button_draft()
            move.write({'partner_bank_id': bank.id, 'invoice_payment_term_id': term.id,
                        'narration': '<p>NCNOTE thank you</p>', 'payment_reference': 'NCREF'})
            move.action_post()
        first, second = moves
        company_before = (self.env.company.invoice_terms, self.env.company.terms_type)
        html = self._html(first)
        for marker in ('AE070331234567890123456', 'NCTERMS', 'NCNOTE'):
            self.assertIn(marker, html, 'default prints Odoo\'s own section %s' % marker)
        setting = self._setting(first, show_bank_details='hide')
        html = self._html(first)
        self.assertNotIn('AE070331234567890123456', html)
        self.assertIn('NCTERMS', html)
        self.assertIn('NCNOTE', html)
        setting.write({'show_bank_details': 'default', 'show_payment_terms': 'hide'})
        html = self._html(first)
        self.assertIn('AE070331234567890123456', html)
        self.assertNotIn('NCTERMS', html)
        self.assertIn('NCNOTE', html)
        setting.write({'show_payment_terms': 'show', 'show_notes': 'hide'})
        html = self._html(first)
        self.assertIn('NCTERMS', html)
        self.assertNotIn('NCNOTE', html)
        setting.write({'show_bank_details': 'hide', 'show_payment_terms': 'hide', 'show_notes': 'hide'})
        other = self._html(second)                      # the other invoice is untouched
        for marker in ('AE070331234567890123456', 'NCTERMS', 'NCNOTE'):
            self.assertIn(marker, other)
        self.assertEqual((self.env.company.invoice_terms, self.env.company.terms_type), company_before)
        self.assertEqual((first.narration, first.partner_bank_id, first.invoice_payment_term_id),
                         (second.narration, bank, term), 'presentation only: the document data is unchanged')

    def test_reset_returns_to_the_defaults(self):
        setting = self._setting(self.inv_a, title='X')
        setting.action_reset()
        self.assertFalse(self.inv_a._nc_print_setting())
        self.assertNotEqual(self.DocType._nc_print_options(self.inv_a)['title'], 'X')

    def test_access_follows_the_document(self):
        reader = new_test_user(self.env, login='ps_reader', groups='account.group_account_readonly')
        with self.assertRaises(AccessError):
            self.env['ncollection.move.print.setting'].with_user(reader).create({'move_id': self.inv_a.id})
        setting = self._setting(self.inv_a)
        with self.assertRaises(ValidationError):
            setting.move_id = self.inv_b
        with self.assertRaises(ValidationError):
            setting.primary_color = 'red'
