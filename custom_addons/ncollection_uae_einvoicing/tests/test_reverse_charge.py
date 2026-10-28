# -*- coding: utf-8 -*-
"""Domestic reverse charge (Guidelines V2.0 §10.5.1): between two VAT registrants, no VAT on the invoice, the goods
type, and a narrative of the reason. Metal scrap is a reverse-charge good with no code in the pinned PINT-AE release
(fatal ibr-006-ae, upstream finding 'metal-scrap-goods-type-missing', OCQ-6): it is recognised and BLOCKED."""

from lxml import etree

from odoo import Command
from odoo.exceptions import ValidationError
from odoo.tests import tagged

from ..lib import codelists
from ..lib.xml_builder import NS_CAC, NS_CBC
from .common import EInvoiceCase

NS = {'cac': NS_CAC, 'cbc': NS_CBC}
NARRATIVE = 'Reverse charge: Cabinet Decision No. 91 of 2023 (electronic devices)'


@tagged('post_install', '-at_install')
class TestReverseChargeOnRealDocuments(EInvoiceCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tax_rc = cls.env['account.tax'].create(
            {'name': 'RC 0% (test)', 'amount': 0.0, 'type_tax_use': 'sale', 'company_id': cls.company.id}
        )
        cls.map_rc = cls.env['ncollection.einvoice.tax.map'].create(
            {
                'company_id': cls.company.id,
                'tax_id': cls.tax_rc.id,
                'pint_category': 'AE',
                'rcm_goods_type': 'DL8.48.8.2',
                'pint_rate': 5.0,
                'rc_narrative': NARRATIVE,
            }
        )
        cls.device = cls.env['product.product'].create(
            {
                'name': 'Smartphone',
                'nc_item_type': 'G',
                'nc_hs_code': '85171300',
                'barcode': '4006381333931',  # a GTIN-13 with a correct check digit (ibr-174-ae, scheme 0160)
            }
        )

    def _rc_invoice(self):
        return self._invoice(
            lines=[
                {
                    'product_id': self.device.id,
                    'quantity': 2,
                    'price_unit': 1500.0,
                    'tax_ids': [Command.set(self.tax_rc.ids)],
                }
            ]
        )

    def test_a_reverse_charge_invoice_carries_no_vat_and_states_its_narrative(self):
        record = self._einvoice(self._rc_invoice())
        record.action_prepare()
        self.assertEqual(record.local_state, 'ready', self.rules(record))
        self.assertEqual(record.type_code, '380')
        record.action_generate_xml()
        root = etree.fromstring(record.xml_attachment_id.raw)
        subtotal = root.find('cac:TaxTotal/cac:TaxSubtotal', NS)
        category = subtotal.find('cac:TaxCategory', NS)
        self.assertEqual(category.findtext('cbc:ID', namespaces=NS), 'AE')
        self.assertEqual(category.findtext('cbc:TaxExemptionReason', namespaces=NS), NARRATIVE)
        self.assertEqual(subtotal.findtext('cbc:TaxAmount', namespaces=NS), '0.00')
        line = root.find('cac:InvoiceLine', NS)
        self.assertEqual(
            line.findtext('cac:Item/cac:CommodityClassification/cbc:NatureCode', namespaces=NS), 'DL8.48.8.2'
        )
        self.assertEqual(line.findtext('cac:ItemPriceExtension/cac:TaxTotal/cbc:TaxAmount', namespaces=NS), '0.00')

    def test_a_reverse_charge_invoice_without_narrative_is_blocked(self):
        self.map_rc.rc_narrative = False
        record = self._einvoice(self._rc_invoice())
        record.action_prepare()
        self.assertEqual(record.local_state, 'draft')
        self.assertIn('NCOLLECTION:rcm-narrative-required', self.rules(record))

    def test_metal_scrap_is_recognised_and_blocked_never_invented(self):
        self.map_rc.rcm_goods_type = codelists.METAL_SCRAP
        record = self._einvoice(self._rc_invoice())
        record.action_prepare()
        self.assertEqual(record.local_state, 'draft')
        rules = self.rules(record)
        self.assertIn('NCOLLECTION:rcm-goods-type-not-in-release', rules)
        self.assertNotIn('OFFICIAL:ibr-166-ae', rules, "metal scrap is a known goods type, not a missing one")
        self.assertNotIn(codelists.METAL_SCRAP, codelists.codes('rcm_goods_type'), "never an official code")

    def test_a_reverse_charge_buyer_needs_a_trn(self):
        no_trn = self._partner('Unregistered Trader LLC', tin='1345678904', registry='112345679000004')
        move = self._invoice(
            partner=no_trn,
            lines=[{'product_id': self.device.id, 'quantity': 1, 'price_unit': 1500.0,
                    'tax_ids': [Command.set(self.tax_rc.ids)]}],
        )
        record = self._einvoice(move)
        record.action_prepare()
        self.assertIn('OFFICIAL:ibr-103-ae', self.rules(record))

    def test_a_reverse_charge_map_needs_goods_type_and_shown_rate(self):
        unmapped = self.env['account.tax'].create(
            {'name': 'RC 0% (unmapped test)', 'amount': 0.0, 'type_tax_use': 'sale', 'company_id': self.company.id}
        )
        with self.assertRaises(ValidationError):
            self.env['ncollection.einvoice.tax.map'].create(
                {'company_id': self.company.id, 'tax_id': unmapped.id, 'pint_category': 'AE', 'pint_rate': 5.0}
            )
