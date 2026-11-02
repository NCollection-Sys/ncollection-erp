# -*- coding: utf-8 -*-
"""Onboarding a company: which of its sales taxes still need a PINT-AE category, BEFORE its first invoice.

The engine never classifies a tax by its rate (a 0% tax may be zero rated, exempt or out of scope), so an unmapped tax
blocks at Prepare. The configuration lists those taxes up front; it reads, it never writes a map."""

from odoo.tests import tagged

from .common import EInvoiceCase


@tagged('post_install', '-at_install')
class TestOnboardingReadiness(EInvoiceCase):

    def _tax(self, name, use='sale', company=None, amount=0.0):
        return self.env['account.tax'].create({
            'name': name, 'amount': amount, 'amount_type': 'percent', 'type_tax_use': use,
            'company_id': (company or self.company).id,
        })

    def test_the_mapped_taxes_are_not_listed_and_an_unmapped_sales_tax_is(self):
        new = self._tax('0% NEW')
        unmapped = self.config.unmapped_sale_tax_ids
        self.assertIn(new, unmapped)
        self.assertFalse((self.tax_s | self.tax_ext | self.tax_z) & unmapped, "a mapped tax is ready")

    def test_purchase_taxes_archived_taxes_and_other_companies_are_not_listed(self):
        purchase = self._tax('5% PURCHASE', use='purchase', amount=5.0)
        archived = self._tax('0% OLD')
        archived.active = False
        other = self._company('Other Seller Co', tin='1987654322', trn='198765432202003', registry='112345678900004')
        foreign = self._tax('0% OTHER CO', company=other)
        unmapped = self.config.unmapped_sale_tax_ids
        self.assertFalse((purchase | archived | foreign) & unmapped)

    def test_listing_is_read_only_and_mapping_a_tax_clears_it(self):
        new = self._tax('5% NEW', amount=5.0)
        Map = self.env['ncollection.einvoice.tax.map']
        before = Map.search_count([])
        self.assertIn(new, self.config.unmapped_sale_tax_ids)
        self.assertIn(new, self.config.unmapped_sale_tax_ids, "reading twice gives the same answer")
        self.assertEqual(Map.search_count([]), before, "listing creates no map: nothing is guessed")
        Map.create({'company_id': self.company.id, 'tax_id': new.id, 'pint_category': 'S'})
        self.config.invalidate_recordset(['unmapped_sale_tax_ids'])
        self.assertNotIn(new, self.config.unmapped_sale_tax_ids)

    def test_an_unmapped_tax_listed_here_is_exactly_what_prepare_blocks(self):
        new = self._tax('0% UNCLASSIFIED')
        self.assertIn(new, self.config.unmapped_sale_tax_ids)
        line = {'product_id': self.product.id, 'quantity': 1, 'price_unit': 10.0, 'tax_ids': [(6, 0, new.ids)]}
        record = self._einvoice(self._invoice(lines=[line]))
        record.action_prepare()
        self.assertIn('NCOLLECTION:tax-unmapped', self.rules(record))
