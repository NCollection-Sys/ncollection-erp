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


@tagged('post_install', '-at_install')
class TestBranchTaxClassification(EInvoiceCase):
    """A branch invoices with its parent company's taxes (Odoo's account.tax checks companies parent_of), and a tax can
    only be classified by the company that owns it. So a branch must reach its parent's maps, and nothing else."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.branch = cls.env['res.company'].create({'name': 'EInv Seller Co - Abu Dhabi branch',
                                                    'parent_id': cls.company.id, 'country_id': cls.ae.id})
        cls.env = cls.env(context=dict(cls.env.context, allowed_company_ids=(cls.branch | cls.company).ids))
        cls.branch_config = cls.env['ncollection.einvoice.config'].create(
            {'company_id': cls.branch.id, 'enabled': True, 'default_payment_means_code': '10'})

    def _draft(self, company, tax):
        # an in-memory invoice of real company / tax records: a bare test branch has no accounting setup of its own
        # (receivable account, payment terms), so posting one would need invented configuration. The lookup reads
        # only the document's taxes and company, which is what this proves.
        return self.env['account.move'].with_company(company).new({
            'move_type': 'out_invoice', 'company_id': company.id, 'partner_id': self.buyer.id,
            'invoice_line_ids': [(0, 0, {'product_id': self.product.id, 'quantity': 1, 'price_unit': 100.0,
                                         'tax_ids': [(6, 0, tax.ids)]})],
        })

    def test_a_branch_invoice_uses_the_parents_classification(self):
        move = self._draft(self.branch, self.tax_s)
        self.assertEqual(move.invoice_line_ids.tax_ids._origin, self.tax_s)
        engine = self.env['ncollection.einvoice'].browse()
        self.assertEqual(engine._nc_tax_maps(move, self.branch), self.map_s.sudo(),
                         "the branch reaches the classification of the parent's tax")

    def test_the_branch_readiness_list_counts_the_parents_mapped_taxes_as_ready(self):
        unmapped = self.branch_config.unmapped_sale_tax_ids
        self.assertFalse((self.tax_s | self.tax_ext | self.tax_z) & unmapped)
        new = self.env['account.tax'].create({'name': '0% PARENT NEW', 'amount': 0.0, 'type_tax_use': 'sale',
                                              'company_id': self.company.id})
        self.branch_config.invalidate_recordset(['unmapped_sale_tax_ids'])
        self.assertIn(new, self.branch_config.unmapped_sale_tax_ids)

    def test_a_parent_never_reads_a_branch_or_sibling_classification(self):
        sibling = self.env['res.company'].create({'name': 'EInv Sibling branch', 'parent_id': self.company.id,
                                                  'country_id': self.ae.id})
        own = self.env['account.tax'].create({'name': '0% SIBLING', 'amount': 0.0, 'type_tax_use': 'sale',
                                              'company_id': sibling.id, 'country_id': self.ae.id,
                                              'tax_group_id': self.tax_z.tax_group_id.id})
        self.env['ncollection.einvoice.tax.map'].create({'company_id': sibling.id, 'tax_id': own.id,
                                                         'pint_category': 'Z'})
        companies = sibling | self.branch | self.company
        self.env = self.env(context=dict(self.env.context, allowed_company_ids=companies.ids))
        move = self._draft(sibling, own)
        engine = self.env['ncollection.einvoice'].browse()
        self.assertEqual(engine._nc_tax_maps(move, sibling).tax_id, own, "the owner of the tax reads its own map")
        for company in (self.company, self.branch):
            with self.subTest(company=company.name):
                self.assertFalse(engine._nc_tax_maps(move, company), "a parent or sibling never reads it")
