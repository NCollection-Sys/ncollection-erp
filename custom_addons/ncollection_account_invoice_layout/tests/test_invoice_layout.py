# -*- coding: utf-8 -*-
from odoo import Command, fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged
from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged('post_install', '-at_install')
class TestInvoiceLayoutCalculations(AccountTestInvoicingCommon):
    """Discount both ways, fixed vs percentage preservation, editable Amount / Total incl. Tax, totals, postings."""

    def _move(self, move_type='out_invoice', qty=3.0, price=100.0, taxes=True, **line_extra):
        vals = {'name': 'Line', 'quantity': qty, 'price_unit': price,
                'tax_ids': [Command.set(self.tax_sale_a.ids if taxes else [])]}
        if move_type.startswith('in_'):
            vals['tax_ids'] = [Command.set(self.tax_purchase_a.ids if taxes else [])]
        vals.update(line_extra)
        return self.env['account.move'].create({
            'move_type': move_type, 'partner_id': self.partner_a.id, 'invoice_date': '2026-03-01',
            'invoice_line_ids': [Command.create(vals)]})

    def _line(self, move):
        return move.invoice_line_ids.filtered(lambda l: l.display_type == 'product')[0]

    # ---------------------------------------------------------------- percentage <-> money
    def test_percentage_gives_the_money_discount_and_the_discounted_amount(self):
        line = self._line(self._move(discount=10.0))
        self.assertEqual(line.nc_discount_amount, 30.0)
        self.assertEqual(line.price_subtotal, 270.0)

    def test_money_discount_gives_the_percentage_and_replaces_the_old_one(self):
        line = self._line(self._move(discount=10.0))
        line.nc_discount_amount = 60.0
        self.assertEqual(line.nc_discount_mode, 'amount')
        self.assertAlmostEqual(line.discount, 20.0, places=2)
        self.assertEqual(line.price_subtotal, 240.0)

    def test_a_fixed_discount_survives_quantity_and_price_changes_in_money(self):
        line = self._line(self._move(discount=10.0))
        line.nc_discount_amount = 45.0
        line.quantity = 5.0
        self.assertEqual(line.nc_discount_amount, 45.0)
        self.assertAlmostEqual(line.discount, 9.0, places=2)
        self.assertEqual(line.price_subtotal, 455.0)
        line.price_unit = 200.0
        self.assertEqual(line.nc_discount_amount, 45.0)
        self.assertEqual(line.price_subtotal, 955.0)

    def test_a_percentage_discount_survives_quantity_and_price_changes_as_a_percentage(self):
        line = self._line(self._move(discount=10.0))
        line.quantity = 5.0
        line.price_unit = 200.0
        self.assertEqual(line.discount, 10.0)
        self.assertEqual(line.nc_discount_amount, 100.0)
        self.assertEqual(line.price_subtotal, 900.0)

    def test_typing_a_percentage_after_a_money_discount_switches_back(self):
        line = self._line(self._move())
        line.nc_discount_amount = 30.0
        line.write({'discount': 5.0})
        self.assertEqual(line.nc_discount_mode, 'percent')
        self.assertEqual(line.nc_discount_amount, 15.0)

    def test_a_money_discount_is_booked_to_the_cent_even_when_its_percentage_is_not_exact(self):
        move = self._move(qty=3.0, price=100.0)
        line = self._line(move)
        line.nc_discount_amount = 10.0            # 3.3333...% of 300, stored as 3.33
        self.assertEqual(line.price_subtotal, 290.0)
        self.assertEqual(move.amount_untaxed, 290.0)
        move.action_post()
        product = move.line_ids.filtered(lambda l: l.display_type == 'product')
        self.assertEqual(-sum(product.mapped('balance')), 290.0)
        self.assertEqual(move.company_currency_id.round(sum(move.line_ids.mapped("balance"))), 0.0)
        receivable = move.line_ids.filtered(lambda l: l.account_id.account_type == 'asset_receivable')
        self.assertEqual(receivable.balance, move.amount_total)

    def test_a_money_discount_beyond_the_line_value_is_refused(self):
        line = self._line(self._move())
        with self.assertRaises(ValidationError):
            line.nc_discount_amount = 301.0

    # ---------------------------------------------------------------- editable Amount
    def test_typing_the_amount_solves_the_unit_price_and_keeps_the_rest(self):
        move = self._move(qty=2.0, price=50.0, discount=5.0)
        line = self._line(move)
        line.nc_amount = 190.0
        self.assertAlmostEqual(line.price_unit, 100.0, places=2)
        self.assertEqual(line.price_subtotal, 190.0)
        self.assertEqual(line.discount, 5.0)
        self.assertEqual(line.quantity, 2.0)
        self.assertEqual(line.tax_ids, self.tax_sale_a)

    def test_typing_the_amount_with_a_fixed_discount_keeps_the_money_discount(self):
        line = self._line(self._move(qty=2.0, price=100.0))
        line.nc_discount_amount = 20.0
        line.nc_amount = 280.0
        self.assertAlmostEqual(line.price_unit, 150.0, places=2)
        self.assertEqual(line.nc_discount_amount, 20.0)
        self.assertEqual(line.price_subtotal, 280.0)

    def test_the_closest_figure_is_used_when_the_price_precision_cannot_hit_the_target(self):
        line = self._line(self._move(qty=3.0, price=10.0, taxes=False))
        line.nc_amount = 100.0                    # 33.33 x 3 = 99.99 / 33.34 x 3 = 100.02
        self.assertAlmostEqual(line.price_subtotal, 100.0, delta=0.02)
        warning = line._nc_onchange_target('amount')
        self.assertTrue(warning and warning['warning'], "the user is told the figure is the closest, not exact")

    # ---------------------------------------------------------------- editable Total incl. Tax
    def test_typing_the_total_incl_tax_solves_the_price_and_recomputes_the_tax(self):
        move = self._move(qty=2.0, price=50.0)
        line = self._line(move)
        line.nc_total_incl = 230.0                # 2 x 100 + 15 %
        self.assertAlmostEqual(line.price_unit, 100.0, places=2)
        self.assertEqual(line.price_subtotal, 200.0)
        self.assertEqual(line.price_total, 230.0)
        self.assertEqual(move.amount_tax, 30.0)
        self.assertEqual(line.tax_ids, self.tax_sale_a)

    def test_typing_the_total_keeps_a_percentage_discount(self):
        line = self._line(self._move(qty=2.0, price=10.0, discount=10.0))
        line.nc_total_incl = 207.0                # 180 + 15 %
        self.assertAlmostEqual(line.price_unit, 100.0, places=2)
        self.assertEqual(line.discount, 10.0)
        self.assertEqual(line.price_total, 207.0)

    def test_impossible_targets_are_refused_with_a_message(self):
        line = self._line(self._move(qty=0.0))
        with self.assertRaises(UserError):
            line.nc_amount = 100.0
        full = self._line(self._move(discount=100.0))
        with self.assertRaises(UserError):
            full.nc_total_incl = 50.0

    def test_a_posted_invoice_is_never_repriced_through_these_fields(self):
        move = self._move()
        move.action_post()
        line = self._line(move)
        for field_name, value in (('nc_amount', 111.0), ('nc_total_incl', 222.0), ('nc_discount_amount', 5.0)):
            with self.assertRaises(UserError), self.cr.savepoint():
                line.write({field_name: value})
        self.assertEqual(move.amount_untaxed, 300.0)

    # ---------------------------------------------------------------- vendor bills, refunds, totals
    def test_a_vendor_bill_behaves_the_same(self):
        move = self._move('in_invoice', qty=2.0, discount=10.0)
        line = self._line(move)
        self.assertEqual(line.nc_discount_amount, 20.0)
        line.nc_total_incl = 207.0
        self.assertEqual(line.price_total, 207.0)
        move.invoice_date = '2026-03-01'
        move.action_post()
        self.assertEqual(move.company_currency_id.round(sum(move.line_ids.mapped("balance"))), 0.0)
        payable = move.line_ids.filtered(lambda l: l.account_id.account_type == 'liability_payable')
        self.assertEqual(-payable.balance, move.amount_total)

    def test_total_discount_and_total_after_discount(self):
        move = self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': self.partner_a.id, 'invoice_date': '2026-03-01',
            'invoice_line_ids': [
                Command.create({'name': 'A', 'quantity': 2, 'price_unit': 100.0, 'discount': 10.0}),
                Command.create({'name': 'B', 'quantity': 1, 'price_unit': 50.0})]})
        self.assertEqual(move.nc_total_discount, 20.0)
        self.assertEqual(move.nc_total_after_discount, move.amount_untaxed)
        self.assertTrue(move.nc_has_discount)
        plain = self._move()
        self.assertFalse(plain.nc_has_discount)

    def test_a_credit_note_copies_the_discount_mode_and_books_the_same_figures(self):
        move = self._move()
        self._line(move).nc_discount_amount = 30.0
        move.action_post()
        reversal = move._reverse_moves([{'invoice_date': fields.Date.today()}])
        refund_line = self._line(reversal)
        self.assertEqual(refund_line.nc_discount_amount, 30.0)
        self.assertEqual(reversal.amount_untaxed, move.amount_untaxed)
        reversal.action_post()
        self.assertEqual(reversal.company_currency_id.round(sum(reversal.line_ids.mapped('balance'))), 0.0)

    def test_ordinary_percentage_invoices_are_unchanged(self):
        move = self._move(discount=12.5)
        line = self._line(move)
        self.assertEqual(line.price_subtotal, 262.5)
        self.assertEqual(line.nc_discount_mode, 'percent')
