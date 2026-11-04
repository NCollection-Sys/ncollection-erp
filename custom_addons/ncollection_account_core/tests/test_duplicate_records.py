# -*- coding: utf-8 -*-
"""DUP-01 on products and payment terms, and on what must NEVER offer a duplicate: payments, electronic invoices,
audit rows. A duplicate of a posted invoice is the existing, tested draft copy (test_duplicate_and_print)."""
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.tests import tagged


@tagged('post_install', '-at_install')
class TestDuplicateRecords(AccountTestInvoicingCommon):

    def test_a_product_duplicate_starts_without_reference_and_barcode(self):
        product = self.env['product.template'].create({
            'name': 'Dup Widget', 'default_code': 'DW-1', 'barcode': '6291041500213', 'list_price': 12.5})
        new = self.env['product.template'].browse(product.action_nc_duplicate()['res_id'])
        self.assertNotEqual(new, product)
        self.assertEqual(new.list_price, 12.5)
        self.assertFalse(new.default_code)
        self.assertFalse(new.barcode)
        self.assertEqual((product.default_code, product.barcode), ('DW-1', '6291041500213'))

    def test_a_payment_term_can_be_duplicated(self):
        term = self.env['account.payment.term'].create({'name': 'Net 21'})
        new = self.env['account.payment.term'].browse(term.action_nc_duplicate()['res_id'])
        self.assertNotEqual(new, term)
        self.assertEqual(term.name, 'Net 21')

    def test_documents_that_must_not_be_copied_do_not_offer_a_duplicate(self):
        for model in ('account.payment', 'account.bank.statement', 'ir.attachment', 'account.move.line'):
            self.assertFalse(hasattr(self.env[model], 'action_nc_duplicate'), model)
        for model in ('ncollection.einvoice', 'ncollection.einvoice.history', 'ncollection.auth.log'):
            if model in self.env:
                self.assertFalse(hasattr(self.env[model], 'action_nc_duplicate'), model)
