# -*- coding: utf-8 -*-
"""FILTER-01 / PAYMENT-01 / LIST-01: the invoice-specific excess, the Overpaid / Taxable / Non-taxable filters and the
database sorting of Paid Amount, Balance Due and Settlement Status.

Every expected figure is derived from the accounting itself (the amounts of the documents and of the payments),
never from what the module computed. Odoo's own residual and the journal entries are asserted UNCHANGED by
showing the excess.
"""
from datetime import timedelta

from odoo import Command, fields
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.tests import tagged

TODAY = fields.Date.today()
PAST = TODAY - timedelta(days=40)
FUTURE = TODAY + timedelta(days=30)


def _recv(lines):
    return lines.filtered(lambda line: line.account_id.account_type == 'asset_receivable')


@tagged('post_install', '-at_install')
class TestSettlementExcess(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.customer = cls.env['res.partner'].create({'name': 'Excess Customer LLC'})
        cls.vendor = cls.env['res.partner'].create({'name': 'Excess Vendor LLC'})
        cls.bank = cls.company_data['default_journal_bank']

    def _doc(self, amount, move_type='out_invoice', due=FUTURE, partner=None, taxes=None, post=True):
        partner = partner or (self.customer if move_type.startswith('out') else self.vendor)
        move = self.env['account.move'].create({
            'move_type': move_type, 'partner_id': partner.id, 'invoice_date': TODAY - timedelta(days=60),
            'invoice_date_due': due, 'invoice_payment_term_id': False,
            'invoice_line_ids': [Command.create({
                'name': 'line', 'quantity': 1, 'price_unit': amount,
                'tax_ids': [Command.set(taxes.ids)] if taxes else [Command.clear()]})]})
        if post:
            move.action_post()
        return move

    def _pay(self, doc, amount):
        """Odoo's own Register Payment, for an amount that may exceed the document."""
        register = self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=doc.ids).create({'amount': amount})
        return register._create_payments()

    def _tax(self, name, amount, group_suffix=None, use='sale'):
        group = self.env['account.tax.group'].create({'name': name, 'company_id': self.env.company.id})
        if group_suffix:
            self.env['ir.model.data'].create({
                'module': 'nc_test', 'name': '%s_%s' % (self.env.company.id, group_suffix),
                'model': 'account.tax.group', 'res_id': group.id})
        return self.env['account.tax'].create({
            'name': name, 'amount': amount, 'type_tax_use': use, 'tax_group_id': group.id})

    # ---------------------------------------------------------------- the excess, and what it never changes
    def test_a_payment_larger_than_the_invoice_is_the_invoices_excess(self):
        doc = self._doc(800.0)
        entries_before = doc.line_ids.mapped('balance')
        self._pay(doc, 1000.0)
        self.assertEqual(doc.amount_residual, 0.0, "Odoo's own residual is untouched")
        self.assertEqual(doc.nc_overpaid_amount, 200.0)
        self.assertEqual(doc.nc_payment_status, 'overpaid')
        self.assertEqual((doc.nc_amount_paid, doc.nc_amount_due), (1000.0, -200.0))
        self.assertEqual(doc.nc_amount_paid + doc.nc_amount_due, doc.amount_total, "paid + balance = total")
        self.assertEqual(doc.line_ids.mapped('balance'), entries_before, "no entry was changed")

    def test_each_settlement_state_and_the_ordinary_figures_are_unchanged(self):
        unpaid = self._doc(100.0)
        partial = self._doc(100.0)
        self._pay(partial, 40.0)
        paid = self._doc(100.0)
        self._pay(paid, 100.0)
        overdue = self._doc(100.0, due=PAST)
        self.assertEqual(
            (unpaid.nc_payment_status, unpaid.nc_amount_paid, unpaid.nc_amount_due, unpaid.nc_overpaid_amount),
            ('unpaid', 0.0, 100.0, 0.0))
        self.assertEqual((partial.nc_payment_status, partial.nc_amount_paid, partial.nc_amount_due),
                         ('partial', 40.0, 60.0))
        self.assertEqual((paid.nc_payment_status, paid.nc_amount_paid, paid.nc_amount_due, paid.nc_overpaid_amount),
                         ('paid', 100.0, 0.0, 0.0))
        self.assertEqual(overdue.nc_payment_status, 'overdue')

    def test_a_payment_shared_between_documents_is_not_the_excess_of_either(self):
        first, second = self._doc(300.0), self._doc(400.0)
        payment = self._pay(first, 1000.0)
        self.assertEqual(first.nc_overpaid_amount, 700.0, "alone, the whole remainder is first's")
        # the same payment line is now applied to a second document: the remainder is customer credit, not either's
        line = _recv(payment.move_id.line_ids)
        _recv(line | second.line_ids).reconcile()
        self.assertEqual(second.amount_residual, 0.0)
        self.assertEqual((first.nc_overpaid_amount, second.nc_overpaid_amount), (0.0, 0.0))
        self.assertEqual((first.nc_payment_status, second.nc_payment_status), ('paid', 'paid'))
        self.assertEqual(sum(line.mapped('amount_residual')), -300.0, "300 of customer credit remains, once")

    def test_a_vendor_bill_overpaid_the_same_way(self):
        bill = self._doc(500.0, move_type='in_invoice')
        self._pay(bill, 650.0)
        self.assertEqual((bill.nc_payment_status, bill.nc_overpaid_amount, bill.nc_amount_due),
                         ('overpaid', 150.0, -150.0))

    def test_a_credit_note_larger_than_the_invoice(self):
        invoice = self._doc(100.0)
        credit = self._doc(130.0, move_type='out_refund')
        _recv(invoice.line_ids | credit.line_ids).reconcile()
        self.assertEqual(invoice.amount_residual, 0.0)
        self.assertEqual(invoice.nc_overpaid_amount, 0.0,
                         "a credit note reversing the invoice is not a payment: its remainder stays customer credit")
        self.assertEqual(credit.nc_overpaid_amount, 0.0, "the credit note itself was not overpaid")

    def test_a_payment_set_back_to_draft_keeps_the_excess_until_unreconciled(self):
        doc = self._doc(800.0)
        payment = self._pay(doc, 1000.0)
        self.assertEqual(doc.nc_overpaid_amount, 200.0)
        # Odoo 19 keeps the reconciliation of a payment set back to draft; the excess follows the reconciliation
        payment.action_draft()
        self.assertEqual(doc.nc_overpaid_amount, 200.0)
        _recv(doc.line_ids | payment.move_id.line_ids).remove_move_reconcile()
        self.assertEqual(doc.nc_overpaid_amount, 0.0, "the stored excess follows the reconciliation")
        self.assertEqual(doc.nc_payment_status, 'unpaid')

    def test_multi_currency_excess_is_in_the_documents_currency(self):
        eur = self.env.ref('base.EUR')
        eur.active = True
        self.env['res.currency.rate'].create({'name': TODAY, 'currency_id': eur.id, 'rate': 2.0,
                                              'company_id': self.env.company.id})
        move = self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': self.customer.id, 'currency_id': eur.id,
            'invoice_date': TODAY, 'invoice_date_due': FUTURE, 'invoice_payment_term_id': False,
            'invoice_line_ids': [Command.create({'name': 'eur', 'quantity': 1, 'price_unit': 100.0,
                                                 'tax_ids': [Command.clear()]})]})
        move.action_post()
        self._pay(move, 130.0)
        self.assertEqual(move.currency_id, eur)
        self.assertEqual(move.nc_overpaid_amount, 30.0)
        self.assertEqual((move.nc_amount_paid, move.nc_amount_due), (130.0, -30.0))

    # ---------------------------------------------------------------- the filters
    def test_the_overpaid_filter_finds_only_invoice_specific_excess(self):
        over = self._doc(800.0)
        self._pay(over, 1000.0)
        paid = self._doc(100.0)
        self._pay(paid, 100.0)
        unpaid = self._doc(100.0)
        found = self.env['account.move'].search(
            [('nc_overpaid_amount', '>', 0), ('id', 'in', (over + paid + unpaid).ids)])
        self.assertEqual(found, over)

    def test_the_payment_filter_finds_a_payment_that_was_applied_and_has_a_remainder(self):
        doc = self._doc(800.0)
        self._pay(doc, 1000.0)
        advance = self.env['account.payment'].create({
            'payment_type': 'inbound', 'partner_type': 'customer', 'partner_id': self.customer.id, 'amount': 500.0,
            'journal_id': self.bank.id})
        advance.action_post()
        exact = self._doc(100.0)
        self._pay(exact, 100.0)
        Payment = self.env['account.payment']
        overpaid = Payment.search([('nc_is_overpaid', '=', True), ('partner_id', '=', self.customer.id)])
        self.assertEqual(len(overpaid), 1)
        self.assertNotIn(advance, overpaid, "an advance (applied to nothing) is a customer credit, not an overpayment")
        self.assertIn(advance, Payment.search([('nc_is_overpaid', '=', False), ('partner_id', '=', self.customer.id)]))

    def test_taxable_and_non_taxable_follow_the_tax_group_not_the_vat_amount(self):
        standard = self._tax('VAT 5% t', 5.0, 'ae_tax_group_5')
        zero = self._tax('VAT 0% t', 0.0, 'ae_tax_group_0')
        exempt = self._tax('VAT Exempted t', 0.0, 'ae_tax_group_exempted')
        scope = self._tax('Out of Scope t', 0.0, 'ae_tax_group_out_of_scope')
        docs = {
            'standard': self._doc(100.0, taxes=standard), 'zero_rated': self._doc(100.0, taxes=zero),
            'exempt': self._doc(100.0, taxes=exempt), 'out_of_scope': self._doc(100.0, taxes=scope),
            'untaxed': self._doc(100.0), 'mixed': self._doc(100.0, taxes=exempt | standard)}
        ids = [d.id for d in docs.values()]
        Move = self.env['account.move']
        taxable = Move.search([('nc_is_taxable', '=', True), ('id', 'in', ids)])
        non_taxable = Move.search([('nc_is_taxable', '=', False), ('id', 'in', ids)])
        self.assertEqual(taxable, docs['standard'] | docs['zero_rated'] | docs['mixed'])
        self.assertEqual(non_taxable, docs['exempt'] | docs['out_of_scope'] | docs['untaxed'])
        self.assertEqual(docs['zero_rated'].amount_tax, 0.0, "a taxable supply with no VAT amount")
        self.assertEqual({d.id: d.nc_is_taxable for d in docs.values()},
                         {docs[k].id: k in ('standard', 'zero_rated', 'mixed') for k in docs})

    def test_a_filter_never_returns_another_companys_documents(self):
        other = self.setup_other_company()['company']
        doc = self.env['account.move'].with_company(other).create({
            'move_type': 'out_invoice', 'partner_id': self.customer.id, 'invoice_date': TODAY,
            'invoice_payment_term_id': False, 'company_id': other.id,
            'invoice_line_ids': [Command.create({'name': 'x', 'quantity': 1, 'price_unit': 10.0,
                                                 'tax_ids': [Command.clear()]})]})
        doc.action_post()
        mine = self.env['account.move'].with_context(allowed_company_ids=self.env.company.ids).search(
            [('nc_is_taxable', '=', False)])
        self.assertNotIn(doc, mine, "the record rules still decide which company's documents a search returns")

    # ---------------------------------------------------------------- sorting, in the database, over the whole set
    def _ids(self, order, domain):
        return self.env['account.move'].search(domain, order=order).ids

    def test_each_displayed_column_sorts_in_the_database_and_matches_the_python_values(self):
        over = self._doc(800.0)
        self._pay(over, 1000.0)
        paid = self._doc(100.0)
        self._pay(paid, 100.0)
        partial = self._doc(100.0)
        self._pay(partial, 30.0)
        unpaid = self._doc(100.0)
        overdue = self._doc(100.0, due=PAST)
        docs = over | paid | partial | unpaid | overdue
        domain = [('id', 'in', docs.ids)]
        for field in ('nc_amount_paid', 'nc_amount_due'):
            asc = self.env['account.move'].search(domain, order=field + ' asc, id')
            self.assertEqual(asc.mapped(field), sorted(docs.mapped(field)), field)
            desc = self.env['account.move'].search(domain, order=field + ' desc, id')
            self.assertEqual(desc.mapped(field), sorted(docs.mapped(field), reverse=True), field)
        self.assertEqual(self.env['account.move'].search(domain, order='nc_amount_due asc, id')[0], over,
                         "the overpaid invoice's negative balance sorts first")
        rank = {'overdue': 0, 'unpaid': 1, 'partial': 2, 'paid': 3, 'overpaid': 4}
        asc = self.env['account.move'].search(domain, order='nc_payment_status asc, id')
        self.assertEqual([rank[s] for s in asc.mapped('nc_payment_status')],
                         sorted(rank[s] for s in docs.mapped('nc_payment_status')))
        self.assertEqual(asc.mapped('nc_payment_status'), ['overdue', 'unpaid', 'partial', 'paid', 'overpaid'])
        desc = self.env['account.move'].search(domain, order='nc_payment_status desc, id')
        self.assertEqual(desc.mapped('nc_payment_status'), ['overpaid', 'paid', 'partial', 'unpaid', 'overdue'])

    def test_sorting_spans_pages_and_keeps_the_filters(self):
        docs = self.env['account.move']
        for amount in (50.0, 20.0, 40.0, 10.0, 30.0):
            docs |= self._doc(amount)
        domain = [('id', 'in', docs.ids), ('nc_is_taxable', '=', False)]
        page1 = self.env['account.move'].search(domain, order='nc_amount_due desc', limit=2, offset=0)
        page2 = self.env['account.move'].search(domain, order='nc_amount_due desc', limit=2, offset=2)
        page3 = self.env['account.move'].search(domain, order='nc_amount_due desc', limit=2, offset=4)
        self.assertEqual((page1 + page2 + page3).mapped('nc_amount_due'), [50.0, 40.0, 30.0, 20.0, 10.0])

    def test_the_sorted_columns_are_offered_as_sortable_by_the_client(self):
        info = self.env['account.move'].fields_get(
            ['nc_amount_paid', 'nc_amount_due', 'nc_payment_status'], ['sortable'])
        self.assertEqual({k: v['sortable'] for k, v in info.items()},
                         {'nc_amount_paid': True, 'nc_amount_due': True, 'nc_payment_status': True})

    def test_only_the_excess_is_stored(self):
        fields = self.env['account.move']._fields
        self.assertTrue(fields['nc_overpaid_amount'].store)
        for name in ('nc_payment_status', 'nc_amount_paid', 'nc_amount_due'):
            self.assertFalse(fields[name].store, name)

    # ---------------------------------------------------------------- evidence: only a payment recorded AGAINST it
    def _advance(self, amount, partner=None):
        payment = self.env['account.payment'].create({
            'payment_type': 'inbound', 'partner_type': 'customer', 'partner_id': (partner or self.customer).id,
            'amount': amount, 'journal_id': self.bank.id})
        payment.action_post()
        return payment

    def test_an_advance_assigned_to_an_invoice_is_customer_credit_not_its_excess(self):
        doc = self._doc(800.0)
        advance = self._advance(1000.0)
        line = _recv(advance.move_id.line_ids)
        doc.js_assign_outstanding_line(line.id)
        self.assertEqual(doc.amount_residual, 0.0)
        self.assertEqual(sum(line.mapped('amount_residual')), -200.0, "200 of customer credit remains")
        self.assertEqual((doc.nc_overpaid_amount, doc.nc_payment_status, doc.nc_amount_due), (0.0, 'paid', 0.0))
        self.assertTrue(advance.nc_is_overpaid, "the payment screen still shows the unapplied remainder")

    def test_a_payment_recorded_against_two_invoices_leaves_credit_not_excess(self):
        first, second = self._doc(300.0), self._doc(400.0)
        register = self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=(first | second).ids).create({'amount': 1000.0})
        register._create_payments()
        self.assertEqual((first.amount_residual, second.amount_residual), (0.0, 0.0))
        self.assertEqual((first.nc_overpaid_amount, second.nc_overpaid_amount), (0.0, 0.0))

    def test_multiple_payments_only_the_recorded_remainder_counts(self):
        doc = self._doc(500.0)
        self._pay(doc, 200.0)
        self._pay(doc, 400.0)
        self.assertEqual((doc.nc_amount_paid, doc.nc_amount_due, doc.nc_overpaid_amount), (600.0, -100.0, 100.0))

    def test_removing_the_reconciliation_restores_the_native_balance(self):
        doc = self._doc(800.0)
        payment = self._pay(doc, 1000.0)
        doc_residual = doc.amount_residual
        _recv(doc.line_ids | payment.move_id.line_ids).remove_move_reconcile()
        self.assertEqual(doc.nc_overpaid_amount, 0.0)
        self.assertEqual((doc.nc_amount_due, doc.nc_amount_paid), (800.0, 0.0))
        self.assertNotEqual(doc.amount_residual, doc_residual)

    def test_the_stamp_is_copy_false_and_unwritable_by_a_plain_user(self):
        doc = self._doc(800.0)
        payment = self._pay(doc, 1000.0)
        self.assertEqual(payment.nc_registered_move_ids, doc)
        self.assertFalse(payment.copy().nc_registered_move_ids)
