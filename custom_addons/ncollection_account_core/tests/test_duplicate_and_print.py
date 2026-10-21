# -*- coding: utf-8 -*-
"""Duplicate on invoices / bills / credit notes / receipts / entries, and Preview = Print for an invoice.

Synthetic data only. The report engine itself is not under test: what is, is that Preview and Print hand the SAME
report and the SAME context to it, so they cannot render two different documents.
"""
from odoo import fields
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import AccessError, UserError
from odoo.tests import HttpCase, tagged


@tagged('post_install', '-at_install')
class TestMoveDuplicate(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.accountant = cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'dup_acct', 'login': 'dup_acct', 'company_id': cls.env.company.id,
            'company_ids': [(6, 0, cls.env.company.ids)],
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id,
                                  cls.env.ref('account.group_account_invoice').id])]})
        cls.reader = cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'dup_reader', 'login': 'dup_reader', 'company_id': cls.env.company.id,
            'company_ids': [(6, 0, cls.env.company.ids)],
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id,
                                  cls.env.ref('account.group_account_readonly').id])]})

    def _document(self, move_type, post=True):
        return self.init_invoice(move_type, post=post, amounts=[100.0, 50.0], invoice_date='2026-03-01')

    def _duplicate(self, move, user=None):
        action = move.with_user(user or self.accountant).action_nc_duplicate()
        return self.env['account.move'].browse(action['res_id']), action

    def test_every_document_family_duplicates_into_a_new_draft(self):
        for move_type in ('out_invoice', 'in_invoice', 'out_refund', 'in_refund', 'out_receipt', 'in_receipt'):
            with self.subTest(move_type=move_type):
                original = self._document(move_type)
                before = (original.state, original.name, original.amount_total, original.write_date)
                new, action = self._duplicate(original)
                self.assertNotEqual(new, original)
                self.assertEqual((action['res_model'], action['view_mode']), ('account.move', 'form'))
                self.assertEqual(new.move_type, move_type)
                self.assertEqual(new.state, 'draft', "a copy is never posted")
                self.assertNotEqual(new.name, original.name, "the number is not reused")
                self.assertFalse(new.posted_before)
                self.assertEqual(new.partner_id, original.partner_id)
                self.assertEqual(new.amount_total, original.amount_total)
                self.assertEqual(len(new.invoice_line_ids), len(original.invoice_line_ids))
                self.assertEqual(sorted(new.invoice_line_ids.mapped('price_unit')),
                                 sorted(original.invoice_line_ids.mapped('price_unit')))
                # the original is untouched
                self.assertEqual((original.state, original.name, original.amount_total, original.write_date), before)

    def test_a_copy_of_a_paid_invoice_carries_no_payment(self):
        invoice = self._document('out_invoice')
        self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=invoice.ids).create({}).action_create_payments()
        self.assertEqual(invoice.payment_state, 'paid')
        new, _action = self._duplicate(invoice)
        self.assertEqual(new.payment_state, 'not_paid')
        self.assertFalse(new.line_ids.mapped('matched_debit_ids') | new.line_ids.mapped('matched_credit_ids'))
        self.assertFalse(new.line_ids.filtered('reconciled'))
        self.assertEqual(invoice.payment_state, 'paid', "the original is still paid")

    def test_each_duplicate_gets_its_own_number_once_posted(self):
        original = self._document('out_invoice')
        first, _a = self._duplicate(original)
        second, _b = self._duplicate(original)
        first.action_post()
        second.action_post()
        self.assertEqual(len({original.name, first.name, second.name}), 3)

    def test_a_cancelled_document_duplicates_into_a_draft_not_a_cancelled_one(self):
        original = self._document('out_invoice', post=False)
        original.button_cancel()
        self.assertEqual(original.state, 'cancel')
        new, _action = self._duplicate(original)
        self.assertEqual(new.state, 'draft')
        self.assertEqual(original.state, 'cancel')

    def test_a_user_who_cannot_create_gets_no_copy(self):
        original = self._document('out_invoice')
        count = self.env['account.move'].search_count([])
        with self.assertRaises(AccessError):
            original.with_user(self.reader).action_nc_duplicate()
        self.assertEqual(self.env['account.move'].search_count([]), count)

    def test_no_privilege_comes_from_the_action(self):
        """The action is not a sudo: the new record is created by, and belongs to, the caller."""
        original = self._document('out_invoice')
        new, _action = self._duplicate(original, self.accountant)
        self.assertEqual(new.create_uid, self.accountant)

    def test_an_entry_generated_by_a_payment_is_not_offered_a_copy(self):
        invoice = self._document('out_invoice')
        self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=invoice.ids).create({}).action_create_payments()
        payment_entry = invoice.line_ids.matched_credit_ids.credit_move_id.move_id
        self.assertTrue(payment_entry.origin_payment_id)
        self.assertFalse(payment_entry.nc_can_duplicate)
        with self.assertRaises(UserError):
            payment_entry.with_user(self.accountant).action_nc_duplicate()

    def test_a_hand_made_journal_entry_can_be_duplicated(self):
        entry = self.env['account.move'].create({
            'move_type': 'entry', 'date': fields.Date.today(),
            'journal_id': self.company_data['default_journal_misc'].id,
            'line_ids': [
                (0, 0, {'name': 'd', 'debit': 10.0, 'credit': 0.0,
                        'account_id': self.company_data['default_account_receivable'].id}),
                (0, 0, {'name': 'c', 'debit': 0.0, 'credit': 10.0,
                        'account_id': self.company_data['default_account_revenue'].id}),
            ]})
        entry.action_post()
        self.assertTrue(entry.nc_can_duplicate)
        new, _action = self._duplicate(entry)
        self.assertEqual(new.state, 'draft')
        self.assertEqual(entry.state, 'posted')

    def test_the_button_is_in_the_form_header_and_translated(self):
        arch = self.env['account.move'].get_views([(False, 'form')])['views']['form']['arch']
        self.assertIn('name="action_nc_duplicate"', arch)
        self.env['res.lang']._activate_lang('ar_001')
        self.env['ir.module.module']._load_module_terms(['ncollection_account_core'], ['ar_001'], overwrite=True)
        arabic = self.env['account.move'].with_context(lang='ar_001').get_views(
            [(False, 'form')])['views']['form']['arch']
        self.assertIn('نسخة مماثلة', arabic)


@tagged('post_install', '-at_install')
class TestPreviewIsPrint(AccountTestInvoicingCommon):
    """Preview and Print are one rendering path: one report, one context, one route."""

    def _print_parts(self, move):
        action = move.action_print_pdf()
        # a company whose document layout was never chosen gets the layout configurator first, carrying the report
        action = action.get('context', {}).get('report_action') or action
        return action['report_name'], action['context']

    def _preview_parts(self, move):
        action = move.preview_invoice()
        # View names the DOCUMENT; the report and context it is rendered with are the mixin's, the same Print uses
        return action, move._nc_print_report().report_name, move._nc_print_context()

    def test_a_posted_invoice_is_never_a_pro_forma_in_either(self):
        move = self.init_invoice('out_invoice', post=True, amounts=[100.0], invoice_date='2026-03-01')
        report_name, print_context = self._print_parts(move)
        action, preview_name, preview_context = self._preview_parts(move)
        self.assertEqual(report_name, preview_name, "Preview and Print render the same report")
        self.assertFalse(print_context['proforma_invoice'])
        self.assertFalse(preview_context['proforma_invoice'])
        self.assertEqual(action['type'], 'ir.actions.act_url')
        self.assertEqual(action['url'], '/ncollection/document/view/account.move/%d' % move.id)
        self.assertEqual(action['target'], 'new', 'View opens in a NEW tab')

    def test_a_draft_is_a_draft_invoice_in_both_never_a_pro_forma(self):
        move = self.init_invoice('out_invoice', post=False, amounts=[100.0], invoice_date='2026-03-01')
        report_name, print_context = self._print_parts(move)
        _action, preview_name, preview_context = self._preview_parts(move)
        self.assertEqual(report_name, preview_name)
        self.assertFalse(print_context['proforma_invoice'])
        self.assertFalse(preview_context['proforma_invoice'])

    def test_the_draft_preview_reads_exactly_as_the_draft_print_from_the_gear_menu(self):
        """A draft has no header Print: it prints from the gear menu, the stock invoice report with no pro-forma flag.
        The Preview must render that same document, titled "Draft Invoice"."""
        import re

        def words(blob):
            text = blob.decode('utf8', 'replace') if isinstance(blob, bytes) else blob
            text = re.sub(r'<script.*?</script>|<style.*?</style>', ' ', text, flags=re.S)
            return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', text)).strip()

        move = self.init_invoice('out_invoice', post=False, amounts=[100.0], invoice_date='2026-03-01')
        gear = self.env.ref('account.account_invoices')
        printed, _kind = gear._render_qweb_pdf(gear.report_name, move.ids)
        _action, preview_name, preview_context = self._preview_parts(move)
        previewed, _kind = self.env['ir.actions.report'].with_context(**preview_context)._render_qweb_pdf(
            preview_name, move.ids)
        self.assertIn('Draft Invoice', words(printed))
        self.assertNotIn('Proforma', words(previewed))
        self.assertEqual(words(previewed), words(printed))

    def test_the_context_is_one_function(self):
        move = self.init_invoice('out_invoice', post=True, amounts=[100.0], invoice_date='2026-03-01')
        self.assertEqual(self._print_parts(move)[1].get('proforma_invoice'),
                         move._nc_print_context()['proforma_invoice'])


@tagged('post_install', '-at_install')
class TestPreviewServesThePrintedDocument(HttpCase, AccountTestInvoicingCommon):

    def test_the_preview_url_returns_the_same_document_as_the_report_engine(self):
        move = self.init_invoice('out_invoice', post=True, amounts=[100.0], invoice_date='2026-03-01')
        action = move.preview_invoice()
        self.env.ref('base.user_admin').write({'company_ids': [(4, move.company_id.id)]})
        self.env.flush_all()
        self.authenticate('admin', 'admin')
        response = self.url_open(action['url'])
        self.assertEqual(response.status_code, 200)
        report = move._nc_print_report().with_context(**move._nc_print_context())
        body, _kind = report._render_qweb_pdf(report.report_name, move.ids)
        # in test mode the engine answers with the HTML it would have printed: compare what a reader would see
        import re

        def words(blob):
            text = blob.decode('utf8', 'replace') if isinstance(blob, bytes) else blob
            text = re.sub(r'<script.*?</script>|<style.*?</style>', ' ', text, flags=re.S)
            return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', text)).strip()

        self.assertIn(move.name, words(response.content))
        self.assertNotIn('Proforma', words(response.content))
        self.assertEqual(words(response.content), words(body))
        # shown by the browser, never forced to download, and named after the document (not the record id)
        self.assertEqual(response.headers['Content-Type'], 'application/pdf')
        disposition = response.headers['Content-Disposition']
        self.assertTrue(disposition.startswith('inline;'), disposition)
        self.assertNotIn('attachment', disposition)
        self.assertIn(move.name.replace('/', '-').replace(' ', '%20'), disposition)
        self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')

    def test_only_a_document_that_opted_in_can_be_viewed_and_only_by_someone_who_may_read_it(self):
        self.env.ref('base.user_admin').write({'company_ids': [(4, self.env.company.id)]})
        self.env.flush_all()
        self.authenticate('admin', 'admin')
        # a model that is not a printable document: refused, whatever the id
        self.assertEqual(self.url_open('/ncollection/document/view/res.users/2').status_code, 404)
        self.assertEqual(self.url_open('/ncollection/document/view/no.such.model/1').status_code, 404)
        self.assertEqual(self.url_open('/ncollection/document/view/account.move/99999999').status_code, 404)
