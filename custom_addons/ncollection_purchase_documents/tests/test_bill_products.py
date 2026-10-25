# -*- coding: utf-8 -*-
"""A vendor bill takes PRODUCTS with or without a purchase order behind it.

Odoo's bill line renderer hides the Product column of every bill by default (only Label was left), so a bill entered by
hand could not pick a product. ncollection_account_core shows it (``optional="show"``); here both scenarios are proven
end to end, through posting: A) PO -> bill, unchanged; B) a manual bill with product, quantity, price and taxes.
Synthetic data only.
"""
from lxml import etree

from odoo import Command, fields
from odoo.tests import Form, TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestVendorBillProducts(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.vendor = cls.env['res.partner'].create({'name': 'Bill Products Vendor'})
        cls.tax = cls.env['account.tax'].create({'name': 'Bill Products VAT 5%', 'amount': 5.0,
                                                 'type_tax_use': 'purchase'})
        cls.product = cls.env['product.product'].create({
            'name': 'Bill Products Part', 'standard_price': 20.0, 'purchase_method': 'purchase',
            'supplier_taxes_id': [Command.set(cls.tax.ids)]})

    def test_the_bill_form_shows_product_and_quantity_by_default(self):
        arch = etree.fromstring(self.env['account.move'].with_context(default_move_type='in_invoice')
                                .get_views([(False, 'form')])['views']['form']['arch'])
        lines = arch.xpath("//field[@name='invoice_line_ids']/list")[0]
        for name in ('product_id', 'quantity', 'name', 'price_unit', 'tax_ids'):
            with self.subTest(column=name):
                column = lines.xpath("./field[@name='%s']" % name)[0]
                self.assertNotEqual(column.get('optional'), 'conditional',
                                    "Odoo's renderer turns 'conditional' into hidden on every bill")
                self.assertNotEqual(column.get('optional'), 'hide')

    def test_b_a_manual_bill_takes_a_product_quantity_price_and_taxes_and_posts(self):
        with Form(self.env['account.move'].with_context(default_move_type='in_invoice')) as bill:
            bill.partner_id = self.vendor
            bill.invoice_date = fields.Date.today()
            with bill.invoice_line_ids.new() as line:
                line.product_id = self.product
                line.quantity = 3
                line.price_unit = 20.0
        move = bill.save()
        line = move.invoice_line_ids
        self.assertEqual((line.product_id, line.quantity, line.price_unit), (self.product, 3, 20.0))
        self.assertEqual(line.tax_ids, self.tax, 'the product brings its purchase taxes')
        self.assertTrue(line.name, 'the label is still there, as the description')
        self.assertFalse(move.invoice_line_ids.purchase_line_id, 'no purchase order behind it')
        move.action_post()
        self.assertEqual(move.state, 'posted')
        self.assertAlmostEqual(move.amount_untaxed, 60.0)
        self.assertAlmostEqual(move.amount_tax, 3.0)
        self.assertAlmostEqual(move.amount_total, 63.0)

    def test_a_a_bill_from_a_purchase_order_still_carries_its_lines_and_posts(self):
        order = self.env['purchase.order'].create({
            'partner_id': self.vendor.id,
            'order_line': [Command.create({'product_id': self.product.id, 'product_qty': 4, 'price_unit': 20.0})]})
        order.button_confirm()
        action = order.action_create_invoice()
        move = self.env['account.move'].browse(action['res_id'])
        line = move.invoice_line_ids
        self.assertEqual((move.move_type, line.product_id, line.quantity), ('in_invoice', self.product, 4))
        self.assertEqual(line.purchase_line_id, order.order_line, 'linked to its purchase order line')
        move.invoice_date = fields.Date.today()
        move.action_post()
        self.assertEqual(move.state, 'posted')
        self.assertAlmostEqual(move.amount_total, 84.0)
        self.assertEqual(order.invoice_status, 'invoiced')
