# -*- coding: utf-8 -*-
from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged
from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged('post_install', '-at_install')
class TestInvoiceAdjustments(AccountTestInvoicingCommon):
    """Freight, global discount (fixed / %), combined with line discounts and taxes; postings stay balanced."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        income = cls.company_data['default_account_revenue']
        expense = cls.company_data['default_account_expense']
        Type = cls.env['ncollection.invoice.adjustment.type']
        cls.freight = Type.create({'name': 'Freight', 'kind': 'charge', 'value_mode': 'fixed', 'account_id': income.id,
                                   'tax_ids': [Command.set(cls.tax_sale_a.ids)], 'applies_to': 'customer'})
        cls.global_pct = Type.create({'name': 'Global discount %', 'kind': 'discount', 'value_mode': 'percent',
                                      'account_id': income.id, 'tax_ids': [Command.set(cls.tax_sale_a.ids)],
                                      'applies_to': 'customer'})
        cls.global_fixed = Type.create({'name': 'Global discount', 'kind': 'discount', 'value_mode': 'fixed',
                                        'account_id': income.id, 'tax_ids': [Command.set(cls.tax_sale_a.ids)]})
        cls.handling = Type.create({'name': 'Handling %', 'kind': 'charge', 'value_mode': 'percent',
                                    'basis': 'lines_after_discount', 'account_id': income.id, 'applies_to': 'customer'})
        cls.bill_freight = Type.create({'name': 'Bill freight', 'kind': 'charge', 'value_mode': 'fixed',
                                        'account_id': expense.id, 'applies_to': 'vendor',
                                        'tax_ids': [Command.set(cls.tax_purchase_a.ids)]})

    def _invoice(self, move_type='out_invoice', lines=((2.0, 100.0, 10.0),)):
        tax = self.tax_sale_a if move_type == 'out_invoice' else self.tax_purchase_a
        return self.env['account.move'].create({
            'move_type': move_type, 'partner_id': self.partner_a.id, 'invoice_date': '2026-03-01',
            'invoice_line_ids': [Command.create({'name': 'L%s' % i, 'quantity': q, 'price_unit': p, 'discount': d,
                                                 'tax_ids': [Command.set(tax.ids)]})
                                 for i, (q, p, d) in enumerate(lines)]})

    def _adjust(self, move, type_, value):
        return self.env['ncollection.invoice.adjustment'].create({'move_id': move.id, 'type_id': type_.id, 'value': value})

    def _balanced(self, move):
        move.action_post()
        self.assertEqual(move.company_currency_id.round(sum(move.line_ids.mapped("balance"))), 0.0)
        account_type = 'asset_receivable' if move.is_sale_document() else 'liability_payable'
        counterpart = move.line_ids.filtered(lambda l: l.account_id.account_type == account_type)
        self.assertEqual(abs(sum(counterpart.mapped('balance'))), move.amount_total)

    def test_freight_is_a_real_taxed_line_that_raises_the_total(self):
        move = self._invoice()                                     # 2 x 100 - 10 % = 180
        self._adjust(move, self.freight, 50.0)
        self.assertEqual(move.amount_untaxed, 230.0)
        self.assertEqual(move.amount_tax, 34.5)                    # 15 % of 230: the freight is taxed
        self.assertEqual(move.amount_total, 264.5)
        line = move.invoice_line_ids.filtered('nc_adjustment_id')
        self.assertEqual((line.price_unit, line.quantity, line.account_id), (50.0, 1.0, self.freight.account_id))
        self._balanced(move)

    def test_a_global_percentage_discount_reduces_the_total_after_the_line_discounts(self):
        move = self._invoice()                                     # P = 180
        self._adjust(move, self.global_pct, 10.0)                  # G = 18
        self.assertEqual(move.amount_untaxed, 162.0)
        self.assertEqual(move.amount_total, 186.3)
        self.assertEqual(move.nc_total_discount, 20.0 + 18.0)      # line discount 20 + invoice discount 18
        self.assertEqual(move.nc_total_after_discount, 162.0)
        self._balanced(move)

    def test_combined_line_discount_global_discount_and_freight(self):
        move = self._invoice(lines=((2.0, 100.0, 10.0), (1.0, 50.0, 0.0)))     # P = 180 + 50 = 230
        self._adjust(move, self.global_fixed, 30.0)
        self._adjust(move, self.freight, 25.0)
        self.assertEqual(move.amount_untaxed, 230.0 - 30.0 + 25.0)
        self.assertEqual(move.nc_total_discount, 20.0 + 30.0)
        self.assertEqual(move.nc_total_after_discount, 200.0)                  # untaxed minus the charge
        self._balanced(move)

    def test_a_percentage_charge_can_use_the_amount_after_the_invoice_discounts(self):
        move = self._invoice()                                     # P = 180
        self._adjust(move, self.global_fixed, 30.0)                # 150
        self._adjust(move, self.handling, 10.0)                    # 10 % of 150 = 15
        self.assertEqual(move.amount_untaxed, 180.0 - 30.0 + 15.0)
        self.assertEqual(move.invoice_line_ids.filtered(lambda l: l.nc_adjustment_id.type_id == self.handling).price_unit, 15.0)

    def test_an_adjustment_never_feeds_its_own_basis(self):
        move = self._invoice()
        freight = self._adjust(move, self.freight, 50.0)
        pct = self._adjust(move, self.global_pct, 10.0)
        self.assertEqual(pct.amount, 18.0, "10 % of the 180 of the product lines, not of 230")
        freight.value = 70.0
        self.assertEqual(pct.amount, 18.0)

    def test_changing_a_line_recalculates_the_percentage_adjustment(self):
        move = self._invoice()
        pct = self._adjust(move, self.global_pct, 10.0)
        move.invoice_line_ids.filtered(lambda l: not l.nc_adjustment_id).price_unit = 200.0   # P = 360
        self.assertEqual(pct.amount, 36.0)
        self.assertEqual(move.amount_untaxed, 324.0)

    def test_a_zero_value_books_no_line_and_removing_the_row_removes_the_line(self):
        move = self._invoice()
        freight = self._adjust(move, self.freight, 0.0)
        self.assertFalse(move.invoice_line_ids.filtered('nc_adjustment_id'))
        freight.value = 40.0
        self.assertTrue(move.invoice_line_ids.filtered('nc_adjustment_id'))
        freight.unlink()
        self.assertFalse(move.invoice_line_ids.filtered('nc_adjustment_id'))
        self.assertEqual(move.amount_untaxed, 180.0)

    def test_the_booked_line_cannot_be_edited_or_deleted_by_hand(self):
        move = self._invoice()
        self._adjust(move, self.freight, 50.0)
        line = move.invoice_line_ids.filtered('nc_adjustment_id')
        with self.assertRaises(UserError):
            line.price_unit = 1.0
        with self.assertRaises(UserError):
            line.unlink()

    def test_a_vendor_bill_takes_a_vendor_adjustment_and_refuses_a_customer_only_one(self):
        bill = self._invoice('in_invoice')
        self._adjust(bill, self.bill_freight, 20.0)
        self.assertEqual(bill.amount_untaxed, 200.0)
        with self.assertRaises(ValidationError):
            self._adjust(bill, self.freight, 5.0)
        self._balanced(bill)

    def test_a_posted_invoice_keeps_its_lines_and_refuses_changes(self):
        move = self._invoice()
        freight = self._adjust(move, self.freight, 50.0)
        move.action_post()
        total = move.amount_total
        with self.assertRaises(UserError):
            freight.value = 99.0
        with self.assertRaises(UserError):
            self._adjust(move, self.global_fixed, 5.0)
        self.assertEqual(move.amount_total, total)

    def test_a_credit_note_books_the_same_adjustments_and_balances(self):
        move = self._invoice()
        self._adjust(move, self.freight, 50.0)
        self._adjust(move, self.global_pct, 10.0)
        move.action_post()
        refund = move._reverse_moves([{'invoice_date': '2026-03-05'}])
        self.assertEqual(refund.amount_untaxed, move.amount_untaxed)
        refund.action_post()
        self.assertEqual(refund.company_currency_id.round(sum(refund.line_ids.mapped("balance"))), 0.0)

    def test_new_invoices_start_with_the_adjustments_marked_for_every_invoice(self):
        self.freight.auto_add = True
        move = self._invoice()
        self.assertEqual(move.nc_adjustment_ids.type_id, self.freight)
        self.assertEqual(move.amount_untaxed, 180.0, "a zero value books nothing")

    def test_a_value_cannot_be_negative_or_a_percentage_above_100(self):
        move = self._invoice()
        with self.assertRaises(ValidationError):
            self._adjust(move, self.freight, -1.0)
        with self.assertRaises(ValidationError):
            self._adjust(move, self.global_pct, 101.0)

    def test_only_accounting_managers_define_adjustments_and_companies_are_isolated(self):
        invoicer = self.env['res.users'].create({
            'name': 'inv_user', 'login': 'inv_user', 'password': 'Inv-User-Pass1!',
            'group_ids': [Command.set([self.env.ref('base.group_user').id, self.env.ref('account.group_account_invoice').id])]})
        types = self.env['ncollection.invoice.adjustment.type'].with_user(invoicer)
        self.assertTrue(types.search([]))
        with self.assertRaises(AccessError):
            types.create({'name': 'x', 'kind': 'charge', 'account_id': self.freight.account_id.id})
        with self.assertRaises(AccessError):
            self.freight.with_user(invoicer).write({'name': 'renamed'})
        other = self.env['res.company'].create({'name': 'Adj Other Co'})
        stranger = self.env['res.users'].create({
            'name': 'adj_stranger', 'login': 'adj_stranger', 'password': 'Adj-Strg-Pass1!',
            'company_id': other.id, 'company_ids': [Command.set([other.id])],
            'group_ids': [Command.set([self.env.ref('base.group_user').id, self.env.ref('account.group_account_manager').id])]})
        self.assertFalse(self.env['ncollection.invoice.adjustment.type'].with_user(stranger).with_company(other).search(
            [('id', '=', self.freight.id)]))

    def test_the_invoice_audit_still_records_the_amount_change_when_an_adjustment_is_added(self):
        if 'ncollection.invoice.audit' not in self.env:
            return      # the audit module is installed together with this one in CI and on every target database
        move = self._invoice()
        self._adjust(move, self.freight, 50.0)
        rows = self.env['ncollection.invoice.audit'].search([('res_id', '=', move.id), ('event', '=', 'amount_changed')])
        self.assertTrue(rows)
        self.assertEqual(rows[0].new_amount_untaxed, 230.0)
