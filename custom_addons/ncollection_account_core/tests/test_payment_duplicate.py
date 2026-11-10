# -*- coding: utf-8 -*-
"""Duplicate on customer and vendor payments, and the Payment Receipt under every Configure Document Layout choice.

Synthetic data only. Every statement about "what the copy carries" reads Odoo's own accounting (the journal entry,
the reconciliation of the counterpart lines), never a value this module stored.
"""
from lxml import html

from odoo import fields
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged


@tagged('post_install', '-at_install')
class TestPaymentDuplicate(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.customer = cls.env['res.partner'].create({'name': 'Synthetic Customer LLC'})
        cls.vendor = cls.env['res.partner'].create({'name': 'Synthetic Vendor LLC'})
        cls.bank = cls.company_data['default_journal_bank']
        groups = lambda *xmlids: [(6, 0, [cls.env.ref(x).id for x in xmlids])]  # noqa: E731
        users = cls.env['res.users'].with_context(no_reset_password=True)
        common = {'company_id': cls.env.company.id, 'company_ids': [(6, 0, cls.env.company.ids)]}
        cls.accountant = users.create(dict(common, name='paydup_acct', login='paydup_acct',
                                           group_ids=groups('base.group_user', 'account.group_account_invoice')))
        cls.reader = users.create(dict(common, name='paydup_reader', login='paydup_reader',
                                       group_ids=groups('base.group_user', 'account.group_account_readonly')))

    def _payment(self, payment_type='inbound', amount=300.0, post=False, **extra):
        partner_type = 'customer' if payment_type == 'inbound' else 'supplier'
        payment = self.env['account.payment'].create(dict({
            'payment_type': payment_type, 'partner_type': partner_type,
            'partner_id': (self.customer if payment_type == 'inbound' else self.vendor).id,
            'journal_id': self.bank.id, 'amount': amount, 'memo': 'Instalment 3 of 12',
            'date': '2026-03-01'}, **extra))
        if post:
            payment.action_post()
        return payment

    def _duplicate(self, payment, user=None):
        action = payment.with_user(user or self.accountant).action_nc_duplicate()
        return self.env['account.payment'].browse(action['res_id']), action

    def _snapshot(self, payment):
        return (payment.state, payment.name, payment.amount, payment.date, payment.memo, payment.move_id,
                payment.is_reconciled, payment.write_date)

    # --- what the copy is -----------------------------------------------------------------------------------------
    def test_customer_and_vendor_drafts_duplicate_into_a_new_draft(self):
        for payment_type in ('inbound', 'outbound'):
            with self.subTest(payment_type=payment_type):
                original = self._payment(payment_type)
                before = self._snapshot(original)
                new, action = self._duplicate(original)
                self.assertNotEqual(new, original)
                self.assertEqual((action['res_model'], action['view_mode'], action['target']),
                                 ('account.payment', 'form', 'current'))
                self.assertEqual(new.state, 'draft')
                self.assertEqual((new.payment_type, new.partner_type), (original.payment_type, original.partner_type))
                self.assertEqual(new.partner_id, original.partner_id)
                self.assertEqual(new.company_id, original.company_id)
                self.assertEqual(new.journal_id, original.journal_id)
                self.assertEqual(new.payment_method_line_id, original.payment_method_line_id)
                self.assertEqual(new.currency_id, original.currency_id)
                self.assertEqual(new.amount, original.amount)
                self.assertEqual(new.memo, original.memo)
                self.assertEqual(new.date, fields.Date.context_today(new), "a copy is a payment to make now")
                self.assertEqual(self._snapshot(original), before, "the original is untouched")

    def test_a_posted_and_reconciled_payment_duplicates_without_its_accounting(self):
        invoice = self.init_invoice('out_invoice', partner=self.customer, post=True, amounts=[300.0],
                                    invoice_date='2026-03-01')
        original = self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=invoice.ids).create({})._create_payments()
        self.assertTrue(original.move_id, "the source really is posted")
        self.assertTrue(original.reconciled_invoice_ids)
        before = self._snapshot(original)
        residual = invoice.amount_residual
        moves = self.env['account.move'].search_count([])
        new, _action = self._duplicate(original)
        self.assertEqual(new.state, 'draft', "never posted automatically")
        self.assertFalse(new.move_id, "no journal entry is created or shared")
        self.assertEqual(self.env['account.move'].search_count([]), moves, "nothing was posted by the duplication")
        self.assertFalse(new.reconciled_invoice_ids)
        self.assertFalse(new.invoice_ids)
        self.assertFalse(new.is_reconciled)
        self.assertFalse(new.is_matched)
        self.assertFalse(new.payment_reference)
        self.assertFalse(new.nc_registered_move_ids, "the 'recorded against' evidence belongs to the original")
        self.assertFalse(new.nc_apply_first_move_id)
        self.assertNotEqual(new.name, original.name)
        self.assertEqual(new.amount, original.amount)
        # the original, its entry and what it paid are exactly as before
        self.assertEqual(self._snapshot(original), before)
        self.assertEqual(invoice.amount_residual, residual)
        self.assertEqual(original.reconciled_invoice_ids, invoice)

    def test_execution_identity_and_evidence_are_not_copied(self):
        original = self._payment('outbound')
        self.env['ir.attachment'].create({'name': 'bank-slip.pdf', 'res_model': 'account.payment',
                                          'res_id': original.id, 'raw': b'%PDF-synthetic'})
        attachments = self.env['ir.attachment'].search_count([('res_model', '=', 'account.payment')])
        new, _action = self._duplicate(original)
        self.assertEqual(self.env['ir.attachment'].search_count([('res_model', '=', 'account.payment')]), attachments,
                         "the original's bank slip is not copied onto another payment")
        self.assertFalse(new.is_sent)
        self.assertEqual(new.nc_stamp_mode, 'default')
        for name in ('payment_transaction_id', 'payment_token_id', 'source_payment_id', 'check_number',
                     'nc_cheque_job_ids'):
            if name in new._fields:
                self.assertFalse(new[name], name)

    def test_an_archived_journal_is_replaced_by_a_usable_one(self):
        spare = self.env['account.journal'].create({'name': 'Spare bank', 'type': 'bank', 'code': 'SPB'})
        original = self._payment('inbound', journal_id=spare.id)
        spare.active = False
        new, _action = self._duplicate(original)
        self.assertTrue(new.journal_id.active)
        self.assertNotEqual(new.journal_id, spare)
        self.assertTrue(new.payment_method_line_id, "Odoo chose a payment method of the new journal")
        self.assertEqual(new.payment_method_line_id.journal_id, new.journal_id)

    def test_the_copy_can_be_confirmed_like_any_new_payment(self):
        original = self._payment('inbound', post=True)
        new, _action = self._duplicate(original)
        new.with_user(self.accountant).action_post()
        self.assertIn(new.state, ('in_process', 'paid'))
        self.assertTrue(new.move_id)
        self.assertNotEqual(new.move_id, original.move_id)

    # --- who may -------------------------------------------------------------------------------------------------
    def test_a_read_only_user_cannot_duplicate(self):
        original = self._payment('inbound')
        count = self.env['account.payment'].search_count([])
        with self.assertRaises(AccessError):
            original.with_user(self.reader).action_nc_duplicate()
        self.assertEqual(self.env['account.payment'].search_count([]), count)

    def test_a_payment_of_another_company_cannot_be_duplicated(self):
        other = self.setup_other_company()
        foreign = self.env['account.payment'].with_company(other['company']).create({
            'payment_type': 'inbound', 'partner_type': 'customer', 'partner_id': self.customer.id,
            'journal_id': other['default_journal_bank'].id, 'amount': 10.0})
        with self.assertRaises(AccessError):
            foreign.with_user(self.accountant).action_nc_duplicate()

    def test_one_side_of_an_internal_transfer_is_refused(self):
        first, second = self._payment('outbound'), self._payment('inbound')
        first.paired_internal_transfer_payment_id = second
        with self.assertRaises(UserError):
            first.with_user(self.accountant).action_nc_duplicate()

    # --- the button ----------------------------------------------------------------------------------------------
    def test_the_payment_form_offers_duplicate_in_its_header(self):
        arch = self.env['account.payment'].with_user(self.accountant).get_views([(False, 'form')])['views']['form'][
            'arch']
        header = html.fromstring(arch).xpath('//header')[0]
        buttons = header.xpath(".//button[@name='action_nc_duplicate']")
        self.assertEqual(len(buttons), 1)
        self.assertEqual(buttons[0].get('string'), 'Duplicate')
        reader_arch = self.env['account.payment'].with_user(self.reader).get_views([(False, 'form')])['views'][
            'form']['arch']
        self.assertNotIn('action_nc_duplicate', reader_arch, "hidden from a user who could not use it")


@tagged('post_install', '-at_install')
class TestPaymentReceiptLayouts(AccountTestInvoicingCommon):
    """The receipt's tables are styled by the Professional layout only; under a stock layout they must take Odoo's own
    report classes (so the layout draws them) and the non-grid blocks must opt out of the layout's table design."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        partner = cls.env['res.partner'].create({'name': 'Synthetic Customer LLC'})
        invoice = cls.init_invoice('out_invoice', partner=partner, post=True, amounts=[500.0],
                                   invoice_date='2026-03-01')
        cls.payment = cls.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=invoice.ids).create({'amount': 300.0})._create_payments()

    def _receipt(self, layout_xmlid):
        self.env.company.external_report_layout_id = self.env.ref(layout_xmlid)
        content, _kind = self.env['ir.actions.report']._render_qweb_html(
            'account.action_report_payment_receipt', self.payment.ids)
        return html.fromstring(content)

    def _classes(self, doc, name):
        tables = doc.xpath("//table[@name='%s']" % name)
        self.assertEqual(len(tables), 1, name)
        return set((tables[0].get('class') or '').split())

    def test_every_stock_layout_gets_layout_drawn_data_tables_and_plain_blocks(self):
        stock = [layout for layout in self.env['report.layout'].search([])
                 if layout.view_id.key != 'ncollection_branding.external_layout_nc']
        self.assertGreaterEqual(len(stock), 7, "Light, Boxed, Bold, Striped, Bubble, Wave, Folder")
        for layout in stock:
            with self.subTest(layout=layout.name):
                doc = self._receipt(layout.view_id.key)
                self.assertTrue(doc.xpath("//div[contains(@class, 'o_nc_receipt_stock')]"))
                for name in ('nc_receipt_info', 'nc_receipt_documents'):
                    self.assertLessEqual({'table', 'o_main_table', 'o_nc_table'}, self._classes(doc, name))
                    self.assertNotIn('o_ignore_layout_styling', self._classes(doc, name))
                for name in ('nc_receipt_amount', 'nc_receipt_totals'):
                    self.assertIn('o_ignore_layout_styling', self._classes(doc, name))
                    self.assertNotIn('table', self._classes(doc, name))
                self.assertNotIn('o_nc_table', self._classes(doc, 'nc_receipt_amount'),
                                 "the amount panel is not a data grid")
                signature = doc.xpath("//table[contains(@class, 'o_nc_signatures')]")[0]
                self.assertIn('o_ignore_layout_styling', signature.get('class'))
                self.assertTrue(doc.xpath("//div[contains(@class, 'o_nc_receipt_stock')]/style"))

    def test_the_professional_layout_is_left_exactly_as_it_was(self):
        if not self.env.ref('ncollection_branding.external_layout_nc', raise_if_not_found=False):
            self.skipTest('ncollection_branding is not installed')
        doc = self._receipt('ncollection_branding.external_layout_nc')
        self.assertFalse(doc.xpath("//div[contains(@class, 'o_nc_receipt_stock')]"))
        self.assertFalse(doc.xpath("//div[contains(@class, 'o_nc_receipt')]/style"))
        self.assertEqual(self._classes(doc, 'nc_receipt_info'), {'o_nc_table'})
        self.assertEqual(self._classes(doc, 'nc_receipt_documents'), {'o_nc_table'})
        self.assertEqual(self._classes(doc, 'nc_receipt_amount'), {'o_nc_table', 'o_nc_amount'})
        self.assertEqual(self._classes(doc, 'nc_receipt_totals'), {'o_nc_totals'})

    def test_the_figures_do_not_depend_on_the_layout(self):
        texts = set()
        for key in ('web.external_layout_standard', 'web.external_layout_bubble', 'web.external_layout_boxed'):
            doc = self._receipt(key)
            totals = doc.xpath("//table[@name='nc_receipt_totals']")[0]
            texts.add(' '.join(totals.text_content().split()))
        self.assertEqual(len(texts), 1, texts)
        self.assertIn('300.00', texts.pop())
