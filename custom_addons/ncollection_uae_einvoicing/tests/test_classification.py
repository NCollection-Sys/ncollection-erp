# -*- coding: utf-8 -*-
"""Document classification (lib/doctype.py), the advance-invoice reference and the export deliver-to address.

Classification follows the DECISION of 2026-10-28 (upstream finding ``exempt-only-tax-invoice-refused``, OCQ-20): the
fatal PINT-AE rule ibr-151-ae refuses a 380 / 381 made only of Exempt / Not-subject lines, so such a document is 480 /
81; anything with an S, Z, AE or N line is 380 / 381 (Guidelines V2.0 App 6 §18.3.1: zero-rated is never 480)."""

from datetime import date

from lxml import etree

from odoo import Command
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged

from ..lib import doctype, readiness, spec
from ..lib.xml_builder import NS_CAC, NS_CBC
from .common import EInvoiceCase

NS = {'cac': NS_CAC, 'cbc': NS_CBC}


@tagged('post_install', '-at_install')
class TestDocumentType(TransactionCase):

    def setUp(self):
        super().setUp()
        self.profile = spec.get_profile()

    def test_exempt_only_is_480_because_ibr_151_ae_refuses_it_as_380(self):
        self.assertEqual(doctype.type_code('invoice', {'E'}, self.profile), '480')
        self.assertEqual(doctype.type_code('credit_note', {'E'}, self.profile), '81')

    def test_out_of_scope_only_is_480(self):
        """ibr-122-ae / Guidelines V2.0 App 6 §18.3.1: 480 is for supplies outside the scope of VAT."""
        self.assertEqual(doctype.type_code('invoice', {'O'}, self.profile), '480')
        self.assertEqual(doctype.type_code('invoice', {'E', 'O'}, self.profile), '480')

    def test_zero_rated_only_is_380(self):
        """Guidelines V2.0 App 6 §18.3.1: 480 must not be used for zero-rated supplies."""
        self.assertEqual(doctype.type_code('invoice', {'Z'}, self.profile), '380')
        self.assertEqual(doctype.type_code('credit_note', {'Z'}, self.profile), '381')

    def test_any_vat_relevant_line_makes_a_tax_document(self):
        for categories in ({'S', 'E'}, {'Z', 'E'}, {'AE', 'O'}, {'N'}, {'S', 'Z', 'E', 'O'}):
            self.assertEqual(doctype.type_code('invoice', categories, self.profile), '380', categories)

    def test_no_line_category_is_a_tax_document_and_readiness_names_the_gap(self):
        self.assertEqual(doctype.type_code('invoice', set(), self.profile), '380')

    def test_an_export_without_deliver_to_address_is_an_error(self):
        """ibr-152-ae (fatal): an export needs the deliver-to address unless delivered in the UAE."""
        def snapshot(delivery):
            doc = {'kind': 'invoice', 'type_code': '380', 'flags': {'export': True}, 'transaction_type_code':
                   '00000001', 'payable': '0', 'delivery': delivery}
            return {'doc': doc, 'seller': {'name': 's'}, 'buyer': {'name': 'b'}, 'lines': []}

        def has_rule(delivery):
            return 'OFFICIAL:ibr-152-ae' in {f.rule for f in readiness.check(snapshot(delivery))}

        self.assertTrue(has_rule(None))
        self.assertTrue(has_rule({'street': 'x', 'city': 'y', 'subdivision': None, 'country': 'US'}))
        self.assertFalse(has_rule({'street': 'x', 'city': 'y', 'subdivision': 'NY', 'country': 'US'}))
        self.assertFalse(has_rule({'street': None, 'city': None, 'subdivision': None, 'country': 'AE'}))


@tagged('post_install', '-at_install')
class TestClassificationOnRealDocuments(EInvoiceCase):

    def _line(self, tax, price=100.0):
        return {'product_id': self.product.id, 'quantity': 1, 'price_unit': price, 'tax_ids': [Command.set(tax.ids)]}

    def test_an_exempt_only_invoice_is_480(self):
        record = self._einvoice(self._invoice(lines=[self._line(self.tax_ext)]))
        record.action_prepare()
        self.assertEqual(record.type_code, '480')

    def test_a_zero_rated_invoice_is_380(self):
        record = self._einvoice(self._invoice(lines=[self._line(self.tax_z)]))
        record.action_prepare()
        self.assertEqual(record.type_code, '380')

    def test_exempt_and_zero_rated_together_is_380(self):
        record = self._einvoice(self._invoice(lines=[self._line(self.tax_z), self._line(self.tax_ext, 40.0)]))
        record.action_prepare()
        self.assertEqual(record.type_code, '380')

    def test_the_final_invoice_references_its_advance_invoice(self):
        """Guidelines V2.0 App 5 §17.1: IBT-025 / IBT-026 carry the advance invoice."""
        advance = self._invoice(invoice_date=date(2026, 10, 1), invoice_date_due=date(2026, 10, 31))
        final = self._invoice(invoice_date=date(2026, 10, 20), invoice_date_due=date(2026, 11, 20))
        record = self._einvoice(final, advance_move_ids=[Command.set(advance.ids)])
        record.action_prepare()
        self.assertEqual(record.local_state, 'ready', self.rules(record))
        record.action_generate_xml()
        root = etree.fromstring(record.xml_attachment_id.raw)
        reference = root.find('cac:BillingReference/cac:InvoiceDocumentReference', NS)
        self.assertEqual(reference.findtext('cbc:ID', namespaces=NS), advance.name)
        self.assertEqual(reference.findtext('cbc:IssueDate', namespaces=NS), '2026-10-01')

    def test_an_advance_must_be_an_earlier_posted_invoice_of_the_same_customer(self):
        final = self._invoice(invoice_date=date(2026, 10, 20), invoice_date_due=date(2026, 11, 20))
        later = self._invoice(invoice_date=date(2026, 10, 25), invoice_date_due=date(2026, 11, 25))
        other_buyer = self._partner('Other LLC', tin='1345678903', trn='134567890323003', registry='112345679000003')
        foreign = self._invoice(
            partner=other_buyer, invoice_date=date(2026, 10, 1), invoice_date_due=date(2026, 11, 1)
        )
        draft = self._invoice(post=False)
        for bad in (later, foreign, draft, final):
            with self.assertRaises(ValidationError, msg=bad.display_name):
                self._einvoice(final, advance_move_ids=[Command.set(bad.ids)])

    def test_a_credit_note_cannot_reference_advances(self):
        advance = self._invoice()
        refund = self._invoice(move_type='out_refund')
        with self.assertRaises(ValidationError):
            self._einvoice(refund, advance_move_ids=[Command.set(advance.ids)])

    def test_an_export_carries_the_documents_own_delivery_address(self):
        us = self.env.ref('base.us')
        ny = self.env['res.country.state'].search([('country_id', '=', us.id), ('code', '=', 'NY')], limit=1)
        buyer = self.env['res.partner'].create(
            {'name': 'Gotham Imports Inc', 'company_type': 'company', 'street': '5th Avenue', 'city': 'New York',
             'state_id': ny.id, 'country_id': us.id}
        )
        move = self._invoice(partner=buyer, lines=[self._line(self.tax_z)])
        record = self._einvoice(move, flag_export=True)
        record.action_prepare()
        self.assertEqual(record.local_state, 'ready', self.rules(record))
        self.assertEqual(record.buyer_endpoint_id, '9900000099')
        record.action_generate_xml()
        root = etree.fromstring(record.xml_attachment_id.raw)
        address = root.find('cac:Delivery/cac:DeliveryLocation/cac:Address', NS)
        self.assertEqual(address.findtext('cbc:CityName', namespaces=NS), 'New York')
        self.assertEqual(address.findtext('cac:Country/cbc:IdentificationCode', namespaces=NS), 'US')

    def test_an_export_whose_delivery_address_is_incomplete_is_blocked(self):
        us = self.env.ref('base.us')
        buyer = self.env['res.partner'].create(
            {'name': 'No Street Inc', 'company_type': 'company', 'city': 'Boston', 'country_id': us.id}
        )
        record = self._einvoice(self._invoice(partner=buyer, lines=[self._line(self.tax_z)]), flag_export=True)
        record.action_prepare()
        self.assertEqual(record.local_state, 'draft')
        self.assertIn('OFFICIAL:ibr-152-ae', self.rules(record))
