# -*- coding: utf-8 -*-
"""Charges & Adjustments are booked as real journal items, but never presented as product lines: the printed item list
leaves them out, the printed Charges & Discounts section shows each once, and a global discount over several VAT rates
reduces each VAT base by its own share. Synthetic data only; every figure is read back from Odoo's own accounting."""
from lxml import html

from odoo import Command
from odoo.tests import Form, tagged
from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged('post_install', '-at_install')
class TestInvoiceAdjustmentSeparation(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        income = cls.company_data['default_account_revenue']
        expense = cls.company_data['default_account_expense']
        Tax = cls.env['account.tax']
        cls.vat5 = cls.tax_sale_a  # the common fixture's sales tax (15 %); named for the line it plays
        cls.rate = cls.vat5.amount / 100.0
        cls.vat0 = Tax.create({'name': 'VAT 0% (test)', 'amount': 0.0, 'type_tax_use': 'sale'})
        cls.vat5_incl = Tax.create({'name': 'VAT 5% incl. (test)', 'amount': 5.0, 'type_tax_use': 'sale',
                                    'price_include_override': 'tax_included'})
        Type = cls.env['ncollection.invoice.adjustment.type']
        cls.freight = Type.create({'name': 'Transportation Fees', 'kind': 'charge', 'value_mode': 'fixed',
                                   'account_id': income.id, 'tax_ids': [Command.set(cls.vat5.ids)],
                                   'applies_to': 'customer'})
        cls.handling = Type.create({'name': 'Handling (no VAT)', 'kind': 'charge', 'value_mode': 'fixed',
                                    'account_id': income.id, 'applies_to': 'customer'})
        cls.discount_pct = Type.create({'name': 'Global discount %', 'kind': 'discount', 'value_mode': 'percent',
                                        'account_id': income.id, 'applies_to': 'customer'})
        cls.discount_fixed = Type.create({'name': 'Global discount', 'kind': 'discount', 'value_mode': 'fixed',
                                          'account_id': income.id, 'applies_to': 'customer'})
        cls.bill_freight = Type.create({'name': 'Bill freight', 'kind': 'charge', 'value_mode': 'fixed',
                                        'account_id': expense.id, 'applies_to': 'vendor',
                                        'tax_ids': [Command.set(cls.tax_purchase_a.ids)]})

    def _invoice(self, lines, move_type='out_invoice'):
        return self.env['account.move'].create({
            'move_type': move_type, 'partner_id': self.partner_a.id, 'invoice_date': '2026-03-01',
            'invoice_line_ids': [Command.create({'name': name, 'quantity': 1.0, 'price_unit': price,
                                                 'tax_ids': [Command.set(taxes.ids)]})
                                 for name, price, taxes in lines],
        })

    def _adjust(self, move, type_, value):
        return self.env['ncollection.invoice.adjustment'].create(
            {'move_id': move.id, 'type_id': type_.id, 'value': value})

    def _tax_by_base(self, move):
        return {line.tax_line_id: (round(abs(line.tax_base_amount), 2), round(abs(line.balance), 2))
                for line in move.line_ids.filtered('tax_line_id')}

    def _balanced(self, move):
        self.assertEqual(move.company_currency_id.round(sum(move.line_ids.mapped('balance'))), 0.0)

    # --- defaults ------------------------------------------------------------------------------------------------
    def test_a_discount_follows_the_taxes_of_the_lines_and_a_charge_its_own(self):
        self.assertEqual(self.discount_pct.tax_mode, 'lines')
        self.assertEqual(self.discount_fixed.tax_mode, 'lines')
        self.assertEqual(self.freight.tax_mode, 'fixed')
        with Form(self.env['ncollection.invoice.adjustment.type']) as form:
            form.name = 'X'
            form.account_id = self.company_data['default_account_revenue']
            form.kind = 'discount'
            self.assertEqual(form.tax_mode, 'lines')
            form.kind = 'charge'
            self.assertEqual(form.tax_mode, 'fixed')

    # --- the global discount over several VAT rates -----------------------------------------------------------
    def test_a_percentage_discount_reduces_each_vat_base_by_its_own_share(self):
        move = self._invoice([('Juice', 300.0, self.vat5), ('Exported', 100.0, self.vat0)])
        self._adjust(move, self.discount_pct, 10.0)
        booked = move.invoice_line_ids.filtered('nc_adjustment_id')
        self.assertEqual(len(booked), 2, "one booked line per VAT group")
        by_tax = {line.tax_ids: line.price_subtotal for line in booked}
        self.assertEqual(by_tax[self.vat5], -30.0)
        self.assertEqual(by_tax[self.vat0], -10.0)
        self.assertEqual(move.amount_untaxed, 360.0)
        self.assertEqual(move.amount_tax, round(270.0 * self.rate, 2), "the VAT of 270 (300 - its 30), not of 360")
        self.assertEqual(move.amount_total, 360.0 + round(270.0 * self.rate, 2))
        move.action_post()
        self._balanced(move)
        self.assertEqual(self._tax_by_base(move)[self.vat5], (270.0, round(270.0 * self.rate, 2)))

    def test_a_fixed_discount_is_split_pro_rata_and_the_parts_add_up_exactly(self):
        move = self._invoice([('A', 100.0, self.vat5), ('B', 200.0, self.vat0), ('C', 33.33, self.vat5)])
        self._adjust(move, self.discount_fixed, 10.0)
        booked = move.invoice_line_ids.filtered('nc_adjustment_id')
        self.assertEqual(round(sum(booked.mapped('price_subtotal')), 2), -10.0)
        self.assertEqual(len(booked), 2)
        self.assertEqual(move.amount_untaxed, 323.33)
        move.action_post()
        self._balanced(move)

    def test_a_charge_with_vat_and_a_charge_without_vat(self):
        move = self._invoice([('Juice', 200.0, self.vat5)])
        self._adjust(move, self.freight, 40.0)
        self._adjust(move, self.handling, 15.0)
        lines = {line.name: line for line in move.invoice_line_ids.filtered('nc_adjustment_id')}
        self.assertEqual(lines['Transportation Fees'].tax_ids, self.vat5)
        self.assertFalse(lines['Handling (no VAT)'].tax_ids)
        self.assertEqual(move.amount_untaxed, 255.0)
        self.assertEqual(move.amount_tax, round(240.0 * self.rate, 2), "VAT of 200 + 40; the handling charge has none")
        move.action_post()
        self._balanced(move)

    def test_a_tax_included_price_books_the_exact_tax_excluded_amount(self):
        type_ = self.freight.copy({'name': 'Freight incl.', 'tax_ids': [Command.set(self.vat5_incl.ids)]})
        move = self._invoice([('Juice', 210.0, self.vat5_incl)])
        self._adjust(move, type_, 40.0)
        line = move.invoice_line_ids.filtered('nc_adjustment_id')
        self.assertEqual(line.price_unit, 42.0)
        self.assertEqual(line.price_subtotal, 40.0)
        move.action_post()
        self._balanced(move)

    def test_changing_and_removing_adjustments_never_leaves_duplicates(self):
        move = self._invoice([('Juice', 300.0, self.vat5), ('Exported', 100.0, self.vat0)])
        discount = self._adjust(move, self.discount_pct, 10.0)
        freight = self._adjust(move, self.freight, 40.0)
        for value in (5.0, 20.0, 10.0):
            discount.value = value
            move.write({'invoice_line_ids': [Command.update(move.invoice_line_ids[0].id, {'quantity': 2.0})]})
            move.write({'invoice_line_ids': [Command.update(move.invoice_line_ids[0].id, {'quantity': 1.0})]})
        self.assertEqual(len(move.invoice_line_ids.filtered(lambda ln: ln.nc_adjustment_id == discount)), 2)
        self.assertEqual(len(move.invoice_line_ids.filtered(lambda ln: ln.nc_adjustment_id == freight)), 1)
        freight.unlink()
        discount.unlink()
        self.assertFalse(move.invoice_line_ids.filtered('nc_adjustment_id'))
        self.assertEqual(move.amount_untaxed, 400.0)

    # --- presentation ----------------------------------------------------------------------------------------
    def _pdf_html(self, move):
        content, _kind = self.env['ir.actions.report']._render_qweb_html('account.account_invoices', move.ids)
        return html.fromstring(content)

    def test_the_printed_item_list_has_no_adjustment_and_the_section_has_each_once(self):
        move = self._invoice([('Apple & Ginger Juice 500ml', 300.0, self.vat5), ('Exported', 100.0, self.vat0)])
        self._adjust(move, self.freight, 40.0)
        self._adjust(move, self.discount_pct, 10.0)
        self.assertNotIn('Transportation Fees', move._get_move_lines_to_report().mapped('name'))
        doc = self._pdf_html(move)
        items = ' '.join(doc.xpath("//table[@name='invoice_line_table']")[0].text_content().split())
        self.assertIn('Apple & Ginger Juice 500ml', items)
        self.assertNotIn('Transportation Fees', items)
        self.assertNotIn('Global discount', items)
        section = doc.xpath("//table[@name='nc_invoice_adjustments']")
        self.assertEqual(len(section), 1)
        rows = section[0].xpath('./tbody/tr')
        self.assertEqual(len(rows), 2, "one row per adjustment, even when split over two VAT groups")
        text = ' '.join(section[0].text_content().replace('\ufeff', '').split())
        self.assertIn('Transportation Fees', text)
        self.assertIn('40.00', text)
        self.assertIn('Global discount % (10%)', text)
        self.assertIn('-40.00', text)
        rows_data = move._nc_report_adjustments()
        self.assertEqual([r['subtotal'] for r in rows_data], [40.0, -40.0])  # in the order of the definitions
        self.assertEqual(sum(r['subtotal'] for r in rows_data) + 400.0, move.amount_untaxed)

    def test_an_invoice_without_adjustments_prints_exactly_as_before(self):
        move = self._invoice([('Juice', 100.0, self.vat5)])
        doc = self._pdf_html(move)
        self.assertFalse(doc.xpath("//table[@name='nc_invoice_adjustments']"))
        self.assertEqual(move._get_move_lines_to_report(), move.invoice_line_ids)

    def test_the_lines_stay_accounting_lines(self):
        move = self._invoice([('Juice', 200.0, self.vat5)])
        self._adjust(move, self.freight, 40.0)
        line = move.invoice_line_ids.filtered('nc_adjustment_id')
        self.assertIn(line, move.line_ids, "a real journal item")
        self.assertEqual(line.display_type, 'product')
        self.assertEqual(line.nc_adjustment_id.line_ids, line)

    # --- credit notes, vendor bills ------------------------------------------------------------------------------
    def test_a_credit_note_carries_the_adjustments_as_adjustments(self):
        move = self._invoice([('Juice', 300.0, self.vat5), ('Exported', 100.0, self.vat0)])
        self._adjust(move, self.freight, 40.0)
        self._adjust(move, self.discount_pct, 10.0)
        move.action_post()
        refund = move._reverse_moves([{'invoice_date': '2026-03-05'}])
        self.assertEqual(refund.nc_adjustment_ids.type_id, self.freight | self.discount_pct)
        self.assertEqual(len(refund.invoice_line_ids.filtered('nc_adjustment_id')), 3)
        self.assertEqual(refund.amount_untaxed, move.amount_untaxed)
        self.assertEqual(refund.amount_tax, move.amount_tax)
        self.assertNotIn('Transportation Fees', refund._get_move_lines_to_report().mapped('name'))
        refund.action_post()
        self._balanced(refund)
        self.assertEqual(refund.amount_total, move.amount_total)

    def test_a_cancelling_reversal_keeps_the_link_on_the_posted_reversal(self):
        move = self._invoice([('Juice', 200.0, self.vat5)])
        self._adjust(move, self.freight, 40.0)
        move.action_post()
        reversal = move._reverse_moves([{'invoice_date': '2026-03-05'}], cancel=True)
        self.assertEqual(reversal.state, 'posted')
        self.assertEqual(reversal.nc_adjustment_ids.type_id, self.freight)
        self.assertTrue(reversal.invoice_line_ids.filtered('nc_adjustment_id'))
        self._balanced(reversal)

    def test_a_vendor_bill_separates_the_same_way(self):
        bill = self._invoice([('Materials', 500.0, self.tax_purchase_a)], move_type='in_invoice')
        self._adjust(bill, self.bill_freight, 25.0)
        self.assertNotIn('Bill freight', bill._get_move_lines_to_report().mapped('name'))
        self.assertEqual(bill._nc_report_adjustments()[0]['subtotal'], 25.0)
        self.assertEqual(bill.amount_untaxed, 525.0)
        bill.action_post()
        self._balanced(bill)

    # --- the form ------------------------------------------------------------------------------------------------
    def test_the_invoice_form_carries_the_explicit_link_the_list_filters_on(self):
        arch = self.env['account.move'].get_views([(False, 'form')])['views']['form']['arch']
        line_list = html.fromstring(arch).xpath("//field[@name='invoice_line_ids']/list")[0]
        self.assertTrue(line_list.xpath("./field[@name='nc_adjustment_id']"))
