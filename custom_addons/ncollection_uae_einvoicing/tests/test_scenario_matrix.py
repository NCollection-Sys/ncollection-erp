# -*- coding: utf-8 -*-
"""The UAE scenario matrix (Guidelines V2.0 §10.4, BTAE-02 transaction type) on REAL posted invoices.

Every scenario flag has a POSITIVE case (the engine reaches XML, and with ``NC_EINVOICE_EXPORT_DIR`` set the XML is
validated by the official rules in ``make einvoice-e2e``: the case names are in REQUIRED_CASES) and a NEGATIVE case
(the field the official rule requires is missing, and readiness reports THAT rule before any XML exists). Combinations
are tested where the official rules allow them, and refused where a fatal rule forbids them (ibr-157-ae: summary,
deemed supply and margin scheme are not allowed on an out-of-scope 480 / 81; ibr-116-ae: a margin-scheme document is
only 'N'). Which rule protects which field is the table in docs/markdown/UAE_EINVOICING.md §6a."""

import json
import os
from datetime import date

from odoo import Command
from odoo.tests import tagged

from .common import EInvoiceCase
from .test_real_move_crosscheck import EXPORT_ENV, REQUIRED_CASES

PRINCIPAL_TRN = '155667745601003'
BENEFICIARY_TRN = '189098765401003'


@tagged('post_install', '-at_install')
class TestScenarioMatrix(EInvoiceCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Tax, Map = cls.env['account.tax'], cls.env['ncollection.einvoice.tax.map']
        cls.tax_margin = Tax.create(
            {'name': 'Margin scheme 0% (matrix)', 'amount': 0.0, 'type_tax_use': 'sale', 'company_id': cls.company.id}
        )
        Map.create({'company_id': cls.company.id, 'tax_id': cls.tax_margin.id, 'pint_category': 'N',
                    'pint_rate': 5.0})
        cls.tax_oos = Tax.create(
            {'name': 'Out of scope 0% (matrix)', 'amount': 0.0, 'type_tax_use': 'sale', 'company_id': cls.company.id}
        )
        Map.create({'company_id': cls.company.id, 'tax_id': cls.tax_oos.id, 'pint_category': 'O'})

    # ---------------------------------------------------------------- helpers
    def _line(self, tax=None, price=100.0, qty=1):
        return {'product_id': self.product.id, 'quantity': qty, 'price_unit': price,
                'tax_ids': [Command.set((tax or self.tax_s).ids)]}

    def _record(self, move=None, **vals):
        record = self._einvoice(move or self._invoice(), **vals)
        record.action_prepare()
        return record

    def _xml(self, case, move=None, **vals):
        record = self._record(move, **vals)
        self.assertEqual(record.local_state, 'ready', sorted(self.rules(record)))
        record.action_generate_xml()
        xml = record.xml_attachment_id.raw
        folder = os.environ.get(EXPORT_ENV)
        if folder:
            kind, expect, rule = REQUIRED_CASES[case][:3]
            with open(os.path.join(folder, case + '.xml'), 'wb') as handle:
                handle.write(xml)
            with open(os.path.join(folder, case + '.json'), 'w', encoding='utf-8') as handle:
                json.dump({'case': case, 'kind': kind, 'expect': expect, 'rule': rule}, handle)
        return record, xml

    def _refused(self, rule, move=None, **vals):
        record = self._record(move, **vals)
        self.assertEqual(record.local_state, 'draft')
        self.assertIn(rule, self.rules(record))
        return record

    # ---------------------------------------------------------------- 1. free zone (1XXXXXXX)
    def test_free_zone(self):
        record, xml = self._xml('real-scenario-free-zone', flag_free_zone=True, beneficiary_id=BENEFICIARY_TRN)
        self.assertEqual(record.transaction_type_code, '10000000')
        self.assertIn(b'<cac:BuyerCustomerParty>', xml)

    def test_free_zone_without_beneficiary(self):
        self._refused('OFFICIAL:ibr-007-ae', flag_free_zone=True)

    # ---------------------------------------------------------------- 2. deemed supply (X1XXXXXX)
    def test_deemed_supply(self):
        record, xml = self._xml('real-scenario-deemed-supply', flag_deemed_supply=True)
        self.assertEqual(record.transaction_type_code, '01000000')
        self.assertEqual(record.buyer_endpoint_id, '9900000097', 'the predefined deemed-supply endpoint')
        self.assertNotIn(b'<cac:PaymentMeans>', xml, 'ibr-191-ae: no payment means for a deemed supply')

    def test_deemed_supply_is_not_allowed_on_a_commercial_invoice(self):
        self._refused('OFFICIAL:ibr-157-ae', self._invoice(lines=[self._line(self.tax_oos)]),
                      flag_deemed_supply=True)

    # ---------------------------------------------------------------- 3. margin scheme (XX1XXXXX, category N)
    def test_margin_scheme(self):
        record, xml = self._xml('real-scenario-margin-scheme', self._invoice(lines=[self._line(self.tax_margin,
                                                                                               price=1000.0)]),
                                flag_margin_scheme=True)
        self.assertEqual(record.transaction_type_code, '00100000')
        self.assertIn(b'<cbc:ID>N</cbc:ID>', xml)
        self.assertIn(b'<cbc:Percent>5</cbc:Percent>', xml)

    def test_margin_scheme_with_a_standard_rated_line(self):
        self._refused('OFFICIAL:ibr-116-ae', flag_margin_scheme=True)

    def test_margin_scheme_is_not_allowed_on_a_commercial_invoice(self):
        self._refused('OFFICIAL:ibr-157-ae', self._invoice(lines=[self._line(self.tax_oos)]),
                      flag_margin_scheme=True)

    def test_a_margin_tax_must_carry_no_vat_and_show_its_rate(self):
        from odoo.exceptions import ValidationError
        bare = self.env['account.tax'].create(
            {'name': 'Margin without rate', 'amount': 0.0, 'type_tax_use': 'sale', 'company_id': self.company.id})
        with self.assertRaises(ValidationError):
            self.env['ncollection.einvoice.tax.map'].create(
                {'company_id': self.company.id, 'tax_id': bare.id, 'pint_category': 'N'})

    # ---------------------------------------------------------------- 4. summary invoice (XXX1XXXX)
    def test_summary_invoice(self):
        record, xml = self._xml('real-scenario-summary', flag_summary=True, period_start=date(2026, 9, 1),
                                period_end=date(2026, 9, 30))
        self.assertEqual(record.transaction_type_code, '00010000')
        self.assertIn(b'<cac:InvoicePeriod>', xml)

    def test_summary_invoice_without_period(self):
        self._refused('OFFICIAL:ibr-138-ae', flag_summary=True)

    def test_summary_is_not_allowed_on_a_commercial_invoice(self):
        self._refused('OFFICIAL:ibr-157-ae', self._invoice(lines=[self._line(self.tax_oos)]), flag_summary=True,
                      period_start=date(2026, 9, 1), period_end=date(2026, 9, 30))

    # ---------------------------------------------------------------- 5. continuous supply (XXXX1XXX)
    def test_continuous_supply(self):
        record, _xml = self._xml('real-scenario-continuous-supply', flag_continuous_supply=True,
                                 billing_frequency='MTH', period_start=date(2026, 9, 1), period_end=date(2026, 9, 30))
        self.assertEqual(record.transaction_type_code, '00001000')

    # ---------------------------------------------------------------- 6. disclosed agent billing (XXXXX1XX)
    def test_disclosed_agent_billing(self):
        record, xml = self._xml('real-scenario-agent-billing', flag_agent_billing=True, principal_id=PRINCIPAL_TRN)
        self.assertEqual(record.transaction_type_code, '00000100')
        self.assertIn(b'<cac:SellerSupplierParty>', xml)

    def test_disclosed_agent_billing_without_principal(self):
        self._refused('OFFICIAL:ibr-137-ae', flag_agent_billing=True)

    def test_the_principal_cannot_be_the_agent(self):
        self._refused('OFFICIAL:ibr-176-ae', flag_agent_billing=True, principal_id=self.company.partner_id.nc_trn)

    # ---------------------------------------------------------------- 7. e-commerce (XXXXXX1X)
    def test_ecommerce(self):
        record, xml = self._xml('real-scenario-ecommerce', flag_ecommerce=True)
        self.assertEqual(record.transaction_type_code, '00000010')
        self.assertIn(b'<cac:Delivery>', xml, 'ibr-142-ae: the deliver-to address')

    def test_ecommerce_without_a_delivery_address(self):
        nowhere = self.env['res.partner'].create({'name': 'Locker', 'parent_id': self.buyer.id, 'type': 'delivery'})
        self._refused('OFFICIAL:ibr-142-ae', self._invoice(partner_shipping_id=nowhere.id), flag_ecommerce=True)

    # ---------------------------------------------------------------- 8. export (XXXXXXX1)
    def test_export_without_a_delivery_address_abroad(self):
        us = self.env.ref('base.us')
        buyer = self.env['res.partner'].create({'name': 'Gotham Imports Inc', 'company_type': 'company',
                                                'country_id': us.id})
        self._refused('OFFICIAL:ibr-152-ae', self._invoice(partner=buyer, lines=[self._line(self.tax_z)]),
                      flag_export=True)

    # ---------------------------------------------------------------- combinations
    def test_free_zone_and_ecommerce_together(self):
        record, _xml = self._xml('real-scenario-free-zone-ecommerce', flag_free_zone=True, flag_ecommerce=True,
                                 beneficiary_id=BENEFICIARY_TRN)
        self.assertEqual(record.transaction_type_code, '10000010')

    def test_summary_and_continuous_supply_together(self):
        record, _xml = self._xml('real-scenario-summary-continuous', flag_summary=True, flag_continuous_supply=True,
                                 billing_frequency='MTH', period_start=date(2026, 9, 1), period_end=date(2026, 9, 30))
        self.assertEqual(record.transaction_type_code, '00011000')

    def test_every_flag_missing_its_data_reports_every_rule(self):
        record = self._record(flag_free_zone=True, flag_agent_billing=True, flag_summary=True)
        self.assertTrue({'OFFICIAL:ibr-007-ae', 'OFFICIAL:ibr-137-ae', 'OFFICIAL:ibr-138-ae'} <= self.rules(record))

    # ---------------------------------------------------------------- tax categories
    def test_out_of_scope_only_is_a_commercial_invoice(self):
        record = self._record(self._invoice(lines=[self._line(self.tax_oos)]))
        self.assertEqual((record.local_state, record.type_code), ('ready', '480'))

    def test_mixed_standard_zero_and_exempt_is_a_tax_invoice(self):
        record = self._record(self._invoice(lines=[self._line(), self._line(self.tax_z), self._line(self.tax_ext)]))
        self.assertEqual((record.local_state, record.type_code), ('ready', '380'))
