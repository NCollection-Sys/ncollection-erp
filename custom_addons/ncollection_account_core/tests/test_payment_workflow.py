# -*- coding: utf-8 -*-
"""Invoices / bills first: payment status, payment allocation through Odoo's reconciliation, and the receipt.

Synthetic data only. Every assertion about "what was paid" reads Odoo's own accounting (residuals, payment state,
partial reconciliations); nothing is checked against a value this module stored.
"""
from datetime import timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import AccessError, UserError
from odoo.tests import HttpCase, tagged

TODAY = fields.Date.today()
PAST = TODAY - timedelta(days=40)
FUTURE = TODAY + timedelta(days=30)


class _Common(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.customer = cls.env['res.partner'].create({'name': 'Synthetic Customer LLC'})
        cls.vendor = cls.env['res.partner'].create({'name': 'Synthetic Vendor LLC'})
        cls.bank = cls.company_data['default_journal_bank']

    def _doc(self, move_type, amount, due=FUTURE, partner=None, post=True, currency=None, company=None,
             invoice_date=None):
        partner = partner or (self.customer if move_type.startswith('out') else self.vendor)
        env = self.env if not company else self.env['account.move'].with_company(company).env
        move = env['account.move'].create({
            'move_type': move_type, 'partner_id': partner.id,
            'invoice_date': invoice_date or TODAY - timedelta(days=60), 'invoice_date_due': due,
            'invoice_payment_term_id': False,
            'currency_id': (currency or (company or self.env.company).currency_id).id,
            'invoice_line_ids': [Command.create({'name': 'Synthetic line', 'quantity': 1, 'price_unit': amount,
                                                 'tax_ids': [Command.clear()]})],
        })
        if post:
            move.action_post()
        return move

    def _payment(self, amount, partner, payment_type='inbound', first=None, currency=None, user=None):
        Payment = self.env['account.payment'].with_user(user) if user else self.env['account.payment']
        return Payment.create({
            'payment_type': payment_type,
            'partner_type': 'customer' if partner == self.customer else 'supplier',
            'partner_id': partner.id, 'amount': amount, 'journal_id': self.bank.id, 'date': TODAY,
            'currency_id': (currency or self.env.company.currency_id).id,
            'nc_apply_first_move_id': first.id if first else False,
        })


@tagged('post_install', '-at_install')
class TestPaymentStatus(_Common):
    """Status, paid amount and amount due come from the accounting, in both directions."""

    def _pay_natively(self, doc, amount):
        """Odoo's own Register Payment (the wizard), not this module's allocation."""
        self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=doc.ids).create({'amount': amount})._create_payments()

    def test_every_state_for_a_customer_invoice_and_a_vendor_bill(self):
        for move_type in ('out_invoice', 'in_invoice'):
            with self.subTest(move_type):
                unpaid = self._doc(move_type, 100.0)
                self.assertEqual((unpaid.nc_payment_status, unpaid.nc_amount_paid, unpaid.nc_amount_due),
                                 ('unpaid', 0.0, 100.0))
                partial = self._doc(move_type, 100.0)
                self._pay_natively(partial, 40.0)
                self.assertEqual((partial.nc_payment_status, partial.nc_amount_paid, partial.nc_amount_due),
                                 ('partial', 40.0, 60.0))
                paid = self._doc(move_type, 100.0)
                self._pay_natively(paid, 100.0)
                self.assertEqual((paid.nc_payment_status, paid.nc_amount_paid, paid.nc_amount_due),
                                 ('paid', 100.0, 0.0))
                overdue = self._doc(move_type, 100.0, due=PAST)
                self.assertEqual(overdue.nc_payment_status, 'overdue')
                self._pay_natively(overdue, 30.0)
                self.assertEqual((overdue.nc_payment_status, overdue.nc_amount_due), ('overdue', 70.0),
                                 "partly paid but past due is still overdue")

    def test_overpaid_when_the_payment_that_settled_it_has_a_remainder(self):
        for move_type, partner, payment_type in (('out_invoice', self.customer, 'inbound'),
                                                 ('in_invoice', self.vendor, 'outbound')):
            with self.subTest(move_type):
                doc = self._doc(move_type, 100.0)
                payment = self._payment(130.0, partner, payment_type, first=doc)
                payment.action_nc_post_and_allocate()
                self.assertEqual(doc.amount_residual, 0.0)
                self.assertEqual(doc.nc_payment_status, 'overpaid')
                self.assertEqual(payment.nc_unallocated_amount, 30.0)

    def test_draft_cancelled_and_plain_entries_have_no_status(self):
        draft = self._doc('out_invoice', 100.0, post=False)
        self.assertFalse(draft.nc_payment_status)
        cancelled = self._doc('out_invoice', 100.0, post=False)
        cancelled.button_cancel()
        self.assertFalse(cancelled.nc_payment_status)
        entry = self.env['account.move'].create({
            'move_type': 'entry', 'date': TODAY, 'journal_id': self.company_data['default_journal_misc'].id,
            'line_ids': [
                Command.create({'name': 'd', 'debit': 10.0, 'credit': 0.0,
                                'account_id': self.company_data['default_account_revenue'].id}),
                Command.create({'name': 'c', 'debit': 0.0, 'credit': 10.0,
                                'account_id': self.company_data['default_account_expense'].id}),
            ]})
        entry.action_post()
        self.assertFalse(entry.nc_payment_status)

    def test_a_user_who_may_not_read_reconciliations_can_still_read_the_status(self):
        """A salesman following an order's invoices has no accounting rights: the list must still open."""
        salesman = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'st_salesman', 'login': 'st_salesman', 'company_id': self.env.company.id,
            'company_ids': [Command.set(self.env.company.ids)],
            'group_ids': [Command.set([self.env.ref('base.group_user').id,
                                       self.env.ref('sales_team.group_sale_salesman').id])]})
        paid = self._doc('out_invoice', 100.0)
        self._pay_natively(paid, 100.0)
        unpaid = self._doc('out_invoice', 100.0)
        for doc, expected in ((paid, {'paid', 'overpaid'}), (unpaid, {'unpaid'})):
            as_salesman = doc.with_user(salesman)
            if as_salesman.has_access('read'):
                self.assertIn(as_salesman.nc_payment_status, expected)
                self.assertEqual(as_salesman.nc_amount_due, doc.amount_residual)

    def test_nothing_is_stored(self):
        for name in ('nc_payment_status', 'nc_amount_paid', 'nc_amount_due'):
            self.assertFalse(self.env['account.move']._fields[name].store, name)

    def test_the_printed_invoice_shows_paid_due_and_status_and_view_is_the_same_document(self):
        doc = self._doc('out_invoice', 100.0)
        self._pay_natively(doc, 25.0)
        report = doc._nc_print_report().with_context(**doc._nc_print_context())
        html, _kind = report._render_qweb_pdf(report.report_name, doc.ids)
        html = html.decode('utf8', 'replace')
        self.assertIn('o_nc_amount_paid', html)
        self.assertIn('o_nc_payment_status', html)
        self.assertIn('Partially Paid', html)
        self.assertEqual(doc.action_nc_view()['url'], '/ncollection/document/view/account.move/%d' % doc.id)


@tagged('post_install', '-at_install')
class TestPaymentAllocation(_Common):
    """Selected document first, then earliest due; the remainder stays an outstanding credit. Both directions."""

    def _cases(self):
        return (('out_invoice', self.customer, 'inbound'), ('in_invoice', self.vendor, 'outbound'))

    def test_selected_document_exact_payment(self):
        for move_type, partner, payment_type in self._cases():
            with self.subTest(move_type):
                older = self._doc(move_type, 50.0, due=PAST)
                chosen = self._doc(move_type, 100.0)
                payment = self._payment(100.0, partner, payment_type, first=chosen)
                payment.action_nc_post_and_allocate()
                self.assertEqual(chosen.payment_state, 'paid')
                self.assertEqual(older.amount_residual, 50.0, "the selected document goes first, even if not oldest")
                self.assertEqual(payment.nc_unallocated_amount, 0.0)

    def test_selected_document_partial_payment(self):
        for move_type, partner, payment_type in self._cases():
            with self.subTest(move_type):
                chosen = self._doc(move_type, 100.0)
                other = self._doc(move_type, 80.0, due=PAST)
                payment = self._payment(60.0, partner, payment_type, first=chosen)
                payment.action_nc_post_and_allocate()
                self.assertEqual((chosen.payment_state, chosen.amount_residual), ('partial', 40.0))
                self.assertEqual(other.amount_residual, 80.0)

    def test_an_excess_continues_to_the_earliest_due_open_document(self):
        for move_type, partner, payment_type in self._cases():
            with self.subTest(move_type):
                chosen = self._doc(move_type, 100.0)
                later = self._doc(move_type, 70.0, due=FUTURE + timedelta(days=10))
                earlier = self._doc(move_type, 50.0, due=PAST)
                payment = self._payment(180.0, partner, payment_type, first=chosen)
                rows, unallocated = payment._nc_allocation_plan()
                self.assertEqual([(r['move'], r['applied']) for r in rows],
                                 [(chosen, 100.0), (earlier, 50.0), (later, 30.0)])
                self.assertEqual(unallocated, 0.0)
                payment.action_nc_post_and_allocate()
                self.assertEqual(chosen.payment_state, 'paid')
                self.assertEqual(earlier.payment_state, 'paid')
                self.assertEqual(later.amount_residual, 40.0)

    def test_no_selection_goes_earliest_due_first(self):
        for move_type, partner, payment_type in self._cases():
            with self.subTest(move_type):
                # created in the reverse order of their due dates: the order is the due date, not the id
                third = self._doc(move_type, 30.0, due=FUTURE + timedelta(days=20))
                second = self._doc(move_type, 20.0, due=FUTURE)
                first = self._doc(move_type, 10.0, due=PAST)
                payment = self._payment(25.0, partner, payment_type)
                payment.action_nc_post_and_allocate()
                self.assertEqual(first.amount_residual, 0.0)
                self.assertEqual(second.amount_residual, 5.0)
                self.assertEqual(third.amount_residual, 30.0)

    def test_everything_settled_and_the_rest_stays_an_outstanding_credit(self):
        for move_type, partner, payment_type in self._cases():
            with self.subTest(move_type):
                one, two = self._doc(move_type, 40.0, due=PAST), self._doc(move_type, 60.0)
                payment = self._payment(150.0, partner, payment_type)
                payment.action_nc_post_and_allocate()
                self.assertEqual((one.payment_state, two.payment_state), ('paid', 'paid'))
                self.assertEqual(payment.nc_unallocated_amount, 50.0)
                # the remainder is a genuine open journal item, offered by Odoo itself to the next document
                nxt = self._doc(move_type, 20.0)
                offered = nxt.invoice_outstanding_credits_debits_widget['content']
                self.assertIn(payment.move_id.id, [row['move_id'] for row in offered])

    def test_the_relationship_is_the_reconciliation_both_ways(self):
        for move_type, partner, payment_type in self._cases():
            with self.subTest(move_type):
                a, b = self._doc(move_type, 30.0, due=PAST), self._doc(move_type, 50.0)
                payment = self._payment(60.0, partner, payment_type)
                payment.action_nc_post_and_allocate()
                rows, unallocated = payment._nc_applied_rows()
                self.assertEqual(sorted((r['move'].id, r['applied']) for r in rows),
                                 sorted([(a.id, 30.0), (b.id, 30.0)]))
                self.assertEqual(unallocated, 0.0)
                self.assertEqual(payment.reconciled_invoice_ids | payment.reconciled_bill_ids, a | b,
                                 "Odoo's own reconciled-documents link agrees")
                widget = b.invoice_payments_widget['content']
                self.assertEqual([round(w['amount'], 2) for w in widget], [30.0],
                                 "the document's own payments list shows the amount applied")
                self.assertIn(payment, b.matched_payment_ids)

    def test_a_partly_settled_document_is_offered_again_and_settled_by_a_second_payment(self):
        for move_type, partner, payment_type in self._cases():
            with self.subTest(move_type):
                doc = self._doc(move_type, 100.0, due=PAST)
                self._payment(30.0, partner, payment_type, first=doc).action_nc_post_and_allocate()
                self.assertEqual((doc.payment_state, doc.amount_residual), ('partial', 70.0))
                second = self._payment(70.0, partner, payment_type)
                self.assertIn(doc, second.nc_eligible_move_ids, "a partly reconciled document is still open")
                rows, unallocated = second._nc_allocation_plan()
                self.assertEqual([(r['move'], r['due'], r['applied']) for r in rows], [(doc, 70.0, 70.0)])
                second.action_nc_post_and_allocate()
                self.assertEqual((doc.payment_state, doc.amount_residual), ('paid', 0.0))
                self.assertEqual(unallocated, 0.0)

    def test_the_preview_shows_open_applied_and_unallocated_and_posting_matches_it(self):
        for move_type, partner, payment_type in self._cases():
            with self.subTest(move_type):
                a, b = self._doc(move_type, 40.0, due=PAST), self._doc(move_type, 50.0)
                payment = self._payment(100.0, partner, payment_type)
                rows, unallocated = payment._nc_allocation_plan()
                html = str(payment.nc_allocation_html)
                for text in (a.display_name, b.display_name, '40.00', '50.00', '10.00'):
                    self.assertIn(text, html)                            # document, open, applied, unallocated
                self.assertEqual(self.env['account.partial.reconcile'].search_count(
                    [('credit_move_id.move_id', '=', payment.move_id.id)]), 0, "a preview reconciles nothing")
                payment.action_nc_post_and_allocate()
                applied, left = payment._nc_applied_rows()
                self.assertEqual(sorted((r['move'].id, r['applied']) for r in applied),
                                 sorted((r['move'].id, r['applied']) for r in rows))
                self.assertEqual(left, unallocated)
                self.assertEqual((a.amount_residual, b.amount_residual), (0.0, 0.0))

    def test_another_companys_open_document_is_never_reconciled(self):
        other = self.setup_other_company()['company']
        self.env.user.company_ids |= other
        mine = self._doc('out_invoice', 50.0, due=PAST)
        foreign = self._doc('out_invoice', 50.0, company=other)
        payment = self._payment(100.0, self.customer)
        payment.action_nc_post_and_allocate()
        self.assertEqual(mine.payment_state, 'paid')
        self.assertEqual(foreign.amount_residual, 50.0)
        self.assertEqual(payment.nc_unallocated_amount, 50.0)

    def test_the_preview_is_the_plan_and_changes_nothing(self):
        doc = self._doc('out_invoice', 100.0)
        payment = self._payment(40.0, self.customer, first=doc)
        before = (doc.amount_residual, self.env['account.partial.reconcile'].search_count([]))
        html = str(payment.nc_allocation_html)
        self.assertIn(doc.name, html)
        self.assertEqual(payment.state, 'draft')
        self.assertEqual((doc.amount_residual, self.env['account.partial.reconcile'].search_count([])), before)

    def test_a_credit_note_is_not_paid_by_a_customer_receipt_but_a_refund_is_applied_to_it(self):
        invoice = self._doc('out_invoice', 100.0, due=PAST)
        credit = self._doc('out_refund', 30.0, due=PAST)
        receipt = self._payment(100.0, self.customer)
        self.assertNotIn(credit, receipt.nc_eligible_move_ids)
        self.assertIn(invoice, receipt.nc_eligible_move_ids)
        refund = self._payment(30.0, self.customer, 'outbound')
        self.assertEqual(refund.nc_eligible_move_ids, credit)
        refund.action_nc_post_and_allocate()
        self.assertEqual(credit.payment_state, 'paid')
        self.assertEqual(invoice.amount_residual, 100.0)

    def test_settled_and_draft_documents_are_never_targets(self):
        settled = self._doc('out_invoice', 50.0, due=PAST)
        self._payment(50.0, self.customer, first=settled).action_nc_post_and_allocate()
        draft = self._doc('out_invoice', 50.0, post=False)
        payment = self._payment(10.0, self.customer)
        self.assertNotIn(settled, payment.nc_eligible_move_ids)
        self.assertNotIn(draft, payment.nc_eligible_move_ids)

    def test_a_document_that_is_not_eligible_cannot_be_forced_in(self):
        other_partner_doc = self._doc('out_invoice', 50.0, partner=self.partner_b)
        payment = self._payment(50.0, self.customer, first=other_partner_doc)
        with self.assertRaises(UserError):
            payment.action_nc_post_and_allocate()
        self.assertEqual(payment.state, 'draft')
        self.assertEqual(other_partner_doc.amount_residual, 50.0)

    def test_a_foreign_currency_invoice_is_settled_by_odoo_with_its_own_rates(self):
        foreign = self.setup_other_currency('EUR')
        doc = self._doc('out_invoice', 100.0, currency=foreign)            # 100 EUR
        payment = self._payment(1000.0, self.customer, first=doc)          # company currency, more than enough
        rows, _unallocated = payment._nc_allocation_plan()
        self.assertEqual(rows[0]['move'], doc)
        payment.action_nc_post_and_allocate()
        self.assertEqual(doc.payment_state, 'paid')
        self.assertTrue(doc.currency_id.is_zero(doc.amount_residual))

    def test_another_companys_documents_are_never_offered(self):
        other = self.setup_other_company()['company']
        self.env.user.company_ids |= other
        foreign_doc = self._doc('out_invoice', 50.0, company=other)
        payment = self._payment(50.0, self.customer)
        self.assertNotIn(foreign_doc, payment.nc_eligible_move_ids)

    def test_a_user_without_payment_rights_cannot_confirm(self):
        doc = self._doc('out_invoice', 50.0)
        payment = self._payment(50.0, self.customer, first=doc)
        reader = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'pay_reader', 'login': 'pay_reader', 'company_id': self.env.company.id,
            'company_ids': [Command.set(self.env.company.ids)],
            'group_ids': [Command.set([self.env.ref('base.group_user').id,
                                       self.env.ref('account.group_account_readonly').id])]})
        with self.assertRaises(AccessError):
            payment.with_user(reader).action_nc_post_and_allocate()
        self.assertEqual(doc.amount_residual, 50.0)

    def test_an_accountant_without_admin_rights_can_confirm_and_apply(self):
        """No sudo anywhere: the right to invoice is enough, as it is for Odoo's own Register Payment."""
        accountant = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'pay_acct', 'login': 'pay_acct', 'company_id': self.env.company.id,
            'company_ids': [Command.set(self.env.company.ids)],
            'group_ids': [Command.set([self.env.ref('base.group_user').id,
                                       self.env.ref('account.group_account_invoice').id])]})
        doc = self._doc('out_invoice', 50.0)
        payment = self._payment(50.0, self.customer, first=doc, user=accountant)
        payment.action_nc_post_and_allocate()
        self.assertEqual(doc.payment_state, 'paid')
        self.assertIn(payment, doc.matched_payment_ids)

    def test_the_list_column_needs_no_plan_and_a_draft_shows_nothing(self):
        doc = self._doc('out_invoice', 50.0)
        draft = self._payment(80.0, self.customer, first=doc)
        self.assertEqual(draft.nc_unallocated_amount, 0.0)
        draft.action_nc_post_and_allocate()
        self.assertEqual(draft.nc_unallocated_amount, 30.0)

    def test_programmatic_posting_is_not_allocated(self):
        """The Register Payment wizard, a list's mass Confirm, online payments: native action_post only."""
        doc = self._doc('out_invoice', 50.0)
        payment = self._payment(50.0, self.customer)
        payment.action_post()
        self.assertEqual(doc.amount_residual, 50.0)


@tagged('post_install', '-at_install')
class TestReceiptViewServesThePrintedDocument(HttpCase, _Common):

    def test_the_view_url_returns_the_receipt_the_report_engine_prints(self):
        import re

        def words(blob):
            text = blob.decode('utf8', 'replace') if isinstance(blob, bytes) else blob
            text = re.sub(r'<script.*?</script>|<style.*?</style>', ' ', text, flags=re.S)
            return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', text)).strip()

        doc = self._doc('out_invoice', 80.0)
        payment = self._payment(80.0, self.customer, first=doc)
        payment.action_nc_post_and_allocate()
        self.env.ref('base.user_admin').write({'company_ids': [Command.link(payment.company_id.id)]})
        self.env.flush_all()
        self.authenticate('admin', 'admin')
        response = self.url_open(payment.action_nc_view()['url'])
        self.assertEqual(response.status_code, 200)
        printed = payment.action_nc_print()
        body, _kind = self.env['ir.actions.report']._render_qweb_pdf(printed['report_name'], payment.ids)
        self.assertIn(payment.name, words(response.content))
        self.assertEqual(words(response.content), words(body))


@tagged('post_install', '-at_install')
class TestDocumentActionsAndReceipt(_Common):

    def test_print_receipt_and_view_are_one_report(self):
        doc = self._doc('out_invoice', 50.0)
        payment = self._payment(50.0, self.customer, first=doc)
        payment.action_nc_post_and_allocate()
        printed = payment.action_nc_print()
        self.assertEqual(printed['report_name'], 'account.report_payment_receipt')
        self.assertEqual(payment.action_nc_view()['url'], '/ncollection/document/view/account.payment/%d' % payment.id)
        self.assertEqual(payment._nc_print_report().report_name, printed['report_name'])
        html, _kind = self.env.ref('account.action_report_payment_receipt')._render_qweb_pdf(
            'account.report_payment_receipt', payment.ids)
        self.assertIn(payment.name, html.decode('utf8', 'replace'))

    def test_view_and_print_of_an_invoice_and_a_bill_are_one_report_and_context(self):
        for move_type in ('out_invoice', 'in_invoice'):
            with self.subTest(move_type):
                doc = self._doc(move_type, 50.0)
                action = doc.action_nc_print()
                action = action.get('context', {}).get('report_action') or action
                self.assertEqual(doc._nc_print_report().report_name, action['report_name'],
                                 'View and Print: one report')
                self.assertEqual(doc.action_nc_view()['url'], '/ncollection/document/view/account.move/%d' % doc.id)
                self.assertEqual(doc.action_nc_view(), {**doc.preview_invoice()})

    def test_edit_needs_the_right_to_change_the_document(self):
        doc = self._doc('out_invoice', 50.0)
        self.assertEqual(doc.action_nc_edit()['res_id'], doc.id)
        reader = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'doc_reader', 'login': 'doc_reader', 'company_id': self.env.company.id,
            'company_ids': [Command.set(self.env.company.ids)],
            'group_ids': [Command.set([self.env.ref('base.group_user').id,
                                       self.env.ref('account.group_account_readonly').id])]})
        with self.assertRaises(AccessError):
            doc.with_user(reader).action_nc_edit()
        self.assertTrue(doc.with_user(reader).action_nc_view()['url'])

    def test_files_are_the_documents_own_attachments_for_invoices_and_payments(self):
        doc = self._doc('out_invoice', 50.0)
        payment = self._payment(50.0, self.customer)
        for record in (doc, payment):
            with self.subTest(record._name):
                self.assertEqual(record.nc_attachment_count, 0)
                self.env['ir.attachment'].create({'name': 'synthetic.txt', 'raw': b'synthetic',
                                                  'res_model': record._name, 'res_id': record.id})
                record.invalidate_recordset(['nc_attachment_count'])
                self.assertEqual(record.nc_attachment_count, 1)
                self.assertEqual([f['name'] for f in record.nc_get_attachments()], ['synthetic.txt'])

    def test_the_lists_and_the_payment_form_carry_the_actions(self):
        lists = self.env['account.move'].get_views([(False, 'list')])['views']['list']['arch']
        for needle in ('action_nc_view', 'action_nc_edit', 'nc_attachment_count', 'nc_payment_status'):
            self.assertIn(needle, lists)
        form = self.env['account.payment'].get_views([(False, 'form')])['views']['form']['arch']
        for needle in ('action_nc_post_and_allocate', 'action_nc_print', 'nc_apply_first_move_id',
                       'nc_allocation_html'):
            self.assertIn(needle, form)


@tagged('post_install', '-at_install')
class TestMultiCurrencyAllocation(_Common):
    """The preview is an estimate across currencies; the reconciliation is Odoo's and does not depend on it."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.eur = cls.setup_other_currency('EUR')       # 1 company-currency unit = 2 EUR (the latest rate)

    def _partials(self):
        return self.env['account.partial.reconcile'].search_count([])

    def _settlement(self, doc):
        lines = doc.line_ids.filtered(lambda line: line.account_id.account_type == 'asset_receivable')
        partials = lines.matched_credit_ids
        return (doc.amount_residual, doc.payment_state,
                sorted((partial.amount, partial.debit_amount_currency) for partial in partials))

    def test_a_same_currency_preview_is_exact_and_says_nothing_about_estimates(self):
        doc = self._doc('out_invoice', 100.0)
        payment = self._payment(60.0, self.customer, first=doc)
        self.assertNotIn('o_nc_allocation_estimate', str(payment.nc_allocation_html))
        self.assertFalse(payment._nc_plan_is_an_estimate(payment._nc_allocation_plan()[0]))

    def test_a_cross_currency_preview_says_it_is_an_estimate(self):
        doc = self._doc('out_invoice', 100.0, currency=self.eur)
        payment = self._payment(30.0, self.customer, first=doc)         # company currency against a EUR invoice
        html = str(payment.nc_allocation_html)
        self.assertIn('o_nc_allocation_estimate', html)
        self.assertIn('Estimate', html)
        self.assertTrue(payment._nc_plan_is_an_estimate(payment._nc_allocation_plan()[0]))

    def test_the_cross_currency_preview_posts_and_reconciles_nothing(self):
        doc = self._doc('out_invoice', 100.0, currency=self.eur)
        before = (doc.amount_residual, doc.payment_state, self._partials(),
                  self.env['account.move'].search_count([('origin_payment_id', '!=', False)]))
        payment = self._payment(30.0, self.customer, first=doc)
        self.assertTrue(str(payment.nc_allocation_html))
        payment.nc_unallocated_amount
        self.env.flush_all()
        self.assertEqual(payment.state, 'draft')
        self.assertFalse(payment.move_id)
        self.assertEqual((doc.amount_residual, doc.payment_state, self._partials(),
                          self.env['account.move'].search_count([('origin_payment_id', '!=', False)])), before)

    def test_confirm_reconciles_through_odoos_own_method_and_native_residuals_decide(self):
        doc = self._doc('out_invoice', 100.0, currency=self.eur)         # 100 EUR = 50 in company currency
        payment = self._payment(30.0, self.customer, first=doc)          # 30 in company currency = 60 EUR
        original = type(doc).js_assign_outstanding_line
        calls = []

        def spy(record, line_id):
            calls.append((record.id, line_id))
            return original(record, line_id)

        with patch.object(type(doc), 'js_assign_outstanding_line', spy):
            payment.action_nc_post_and_allocate()
        self.assertEqual(len(calls), 1, "one native reconcile call, for the selected document")
        self.assertEqual(calls[0][0], doc.id)
        self.assertEqual(calls[0][1], payment._nc_counterpart_lines().id)
        self.assertAlmostEqual(doc.amount_residual, 40.0, places=2, msg="Odoo converted 30 -> 60 EUR")
        self.assertEqual(doc.payment_state, 'partial')
        self.assertEqual(payment.reconciled_invoice_ids, doc, "the native link, not a field of ours")

    def test_the_final_accounting_does_not_depend_on_the_preview_figures(self):
        """Same documents, same payment; the preview functions are made to FAIL if Confirm ever consults them, and the
        outcome is identical to a run where they are untouched: the final accounting is Odoo's alone."""
        consulted = []

        def forbidden(name):
            def stub(*args, **kwargs):
                consulted.append(name)
                raise AssertionError("Confirm must not read the preview figure: %s" % name)
            return stub

        outcomes = []
        for guarded in (False, True):
            doc = self._doc('out_invoice', 100.0, currency=self.eur, partner=self.customer)
            payment = self._payment(30.0, self.customer, first=doc)
            if guarded:
                with patch.object(type(payment), '_nc_allocation_plan', forbidden('plan')), \
                        patch.object(type(payment), '_nc_open_in_payment_currency', forbidden('open amount')), \
                        patch.object(type(payment), '_nc_allocation_table', forbidden('table')):
                    payment.action_nc_post_and_allocate()
            else:
                payment.action_nc_post_and_allocate()
            outcomes.append(self._settlement(doc))
            self.assertEqual(payment._nc_applied_rows()[0][0]['move'], doc)
        self.assertEqual(consulted, [], "the allocation order is the only thing Confirm takes from this module")
        self.assertEqual(outcomes[0], outcomes[1])

    def test_a_euro_payment_on_a_euro_invoice_is_exact_and_settles_it(self):
        doc = self._doc('out_invoice', 100.0, currency=self.eur)
        payment = self._payment(100.0, self.customer, first=doc, currency=self.eur)
        self.assertFalse(payment._nc_plan_is_an_estimate(payment._nc_allocation_plan()[0]))
        payment.action_nc_post_and_allocate()
        self.assertEqual(doc.payment_state, 'paid')

    def test_a_vendor_cross_currency_payment_is_the_mirror(self):
        bill = self._doc('in_invoice', 100.0, currency=self.eur)
        payment = self._payment(30.0, self.vendor, 'outbound', first=bill)
        self.assertIn('o_nc_allocation_estimate', str(payment.nc_allocation_html))
        payment.action_nc_post_and_allocate()
        self.assertAlmostEqual(bill.amount_residual, 40.0, places=2)
        self.assertEqual(bill.payment_state, 'partial')


@tagged('post_install', '-at_install')
class TestViewEqualsPrintInBothLanguages(HttpCase, _Common):
    """The tab View opens and the report Print renders read the same, for each accounting document, EN and AR."""

    @staticmethod
    def _words(blob):
        import re
        text = blob.decode('utf8', 'replace') if isinstance(blob, bytes) else blob
        text = re.sub(r'<script.*?</script>|<style.*?</style>', ' ', text, flags=re.S)
        return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', text)).strip()

    def _same(self, record, print_action):
        """View's URL, fetched as a browser would, against the report engine called with Print's report."""
        response = self.url_open(record.action_nc_view()['url'])
        self.assertEqual(response.status_code, 200)
        action = print_action.get('context', {}).get('report_action') or print_action
        context = {k: v for k, v in (action.get('context') or {}).items() if k == 'proforma_invoice'}
        body, _kind = self.env['ir.actions.report'].with_context(**context)._render_qweb_pdf(
            action['report_name'], record.ids)
        viewed, printed = self._words(response.content), self._words(body)
        self.assertTrue(viewed)
        self.assertEqual(viewed, printed)
        return viewed

    def test_invoice_bill_and_receipt_in_english_and_arabic(self):
        import re
        self.env['res.lang']._activate_lang('ar_001')
        self.env['ir.module.module']._load_module_terms(
            ['account', 'ncollection_account_core'], ['ar_001'], overwrite=True)
        self.env.ref('base.user_admin').write({'company_ids': [Command.link(self.env.company.id)]})
        for lang in ('en_US', 'ar_001'):
            self.customer.lang = lang
            self.vendor.lang = lang
            invoice = self._doc('out_invoice', 80.0)
            bill = self._doc('in_invoice', 40.0)
            payment = self._payment(80.0, self.customer, first=invoice)
            payment.action_nc_post_and_allocate()
            self.env.flush_all()
            self.authenticate('admin', 'admin')
            for record, print_action in ((invoice, invoice.action_nc_print()), (bill, bill.action_nc_print()),
                                         (payment, payment.action_nc_print())):
                with self.subTest(lang=lang, model=record._name, record=record.display_name):
                    words = self._same(record, print_action)
                    self.assertEqual(bool(re.search('[؀-ۿ]', words)), lang == 'ar_001',
                                     "the document is in the partner language, the same in View and Print")
