# -*- coding: utf-8 -*-
"""Printed file names (PRINT-01) and the line amounts of invoices and bills (LINE-01 / LINE-02)."""
from lxml import etree

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.addons.ncollection_core.controllers.document_view import pdf_filename
from odoo.tests import tagged


@tagged('post_install', '-at_install')
class TestPrintedFileNames(AccountTestInvoicingCommon):

    def test_each_kind_of_accounting_document_is_named_by_what_it_is_and_its_number(self):
        expected = {
            'out_invoice': 'Customer Invoice', 'out_refund': 'Customer Credit Note', 'in_invoice': 'Vendor Bill',
            'in_refund': 'Vendor Credit Note', 'out_receipt': 'Sales Receipt', 'in_receipt': 'Purchase Receipt',
        }
        for move_type, label in expected.items():
            with self.subTest(move_type=move_type):
                move = self.init_invoice(move_type, post=True, amounts=[100.0], invoice_date='2026-03-01')
                name = move._nc_print_filename()
                self.assertEqual(name, '%s %s' % (label, move.name.replace('/', '-')))
                self.assertIn('/', move.name, "the stored number is untouched")

    def test_the_invoice_reports_print_and_view_under_that_name(self):
        move = self.init_invoice('in_invoice', post=True, amounts=[100.0], invoice_date='2026-03-01')
        for xmlid in ('account.account_invoices', 'account.account_invoices_without_payment'):
            report = self.env.ref(xmlid)
            self.assertEqual(report.print_report_name, 'object._nc_print_filename()')
            self.assertEqual(pdf_filename(report, move), move._nc_print_filename() + '.pdf')

    def test_a_draft_and_a_hostile_number_still_give_a_safe_name(self):
        draft = self.init_invoice('out_invoice', post=False, amounts=[10.0])
        self.assertTrue(draft._nc_print_filename().startswith('Customer Invoice'))
        draft.name = 'A/B\\C"D<E>\r\nF'
        name = draft._nc_print_filename()
        for bad in '/\\"<>\r\n':
            self.assertNotIn(bad, name)


@tagged('post_install', '-at_install')
class TestInvoiceLineAmounts(AccountTestInvoicingCommon):

    def _line_columns(self):
        arch = self.env['account.move'].get_views([(False, 'form')])['views']['form']['arch']
        line_list = etree.fromstring(arch).xpath("//field[@name='invoice_line_ids']/list")[0]
        return [(f.get('name'), f.get('string')) for f in line_list.findall('field')]

    def test_amount_follows_price_and_the_tax_included_total_follows_amount(self):
        names = [name for name, _label in self._line_columns()]
        price = names.index('price_unit')
        self.assertEqual(names[price + 1:price + 4], ['price_subtotal', 'price_total', 'price_total'])
        self.assertIn(('price_total', 'Total incl. Tax'), self._line_columns())
        for kept in ('quantity', 'discount', 'tax_ids', 'product_id', 'name'):
            self.assertIn(kept, names, "no business column is removed")

    def test_the_tax_included_line_total_is_odoos_own_and_reconciles(self):
        tax_5 = self.env['account.tax'].create({'name': 'LINE 5%', 'amount': 5.0, 'type_tax_use': 'sale'})
        tax_10 = self.env['account.tax'].create({'name': 'LINE 10%', 'amount': 10.0, 'type_tax_use': 'sale'})
        move = self.init_invoice('out_invoice', post=False, amounts=[100.0, 33.33, 50.0])
        line_5, line_mix, line_zero = move.invoice_line_ids
        line_5.write({'tax_ids': [(6, 0, tax_5.ids)], 'quantity': 3, 'discount': 10.0})
        line_mix.write({'tax_ids': [(6, 0, (tax_5 | tax_10).ids)]})
        line_zero.write({'tax_ids': [(5, 0, 0)]})
        self.assertAlmostEqual(line_5.price_subtotal, 270.0)
        self.assertAlmostEqual(line_5.price_total, 283.5)
        self.assertAlmostEqual(line_mix.price_total, round(33.33 * 1.15, 2))
        self.assertAlmostEqual(line_zero.price_total, line_zero.price_subtotal)
        self.assertAlmostEqual(sum(move.invoice_line_ids.mapped('price_total')), move.amount_total, places=2)
