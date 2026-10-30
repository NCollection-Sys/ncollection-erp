# -*- coding: utf-8 -*-
"""R05: a buyer OUTSIDE the UAE with only a foreign tax identifier (Guidelines V2.0 §18.2.3).

IBT-046 carries the foreign identifier with its ISO/IEC 6523 ICD scheme, IBT-049 is the predefined 9900000098, and
IBT-048 (VAT identifier) is not populated. The scheme must be on the ICD list the pinned rule ibr-cl-10 enforces."""

import json
import os

from odoo import Command
from odoo.exceptions import ValidationError
from odoo.tests import tagged

from ..lib import codelists
from .common import EInvoiceCase
from .test_real_move_crosscheck import EXPORT_ENV, REQUIRED_CASES


@tagged('post_install', '-at_install')
class TestForeignBuyer(EInvoiceCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.de = cls.env.ref('base.de')
        cls.foreigner = cls.env['res.partner'].create({
            'name': 'Berlin Consulting GmbH', 'company_type': 'company', 'street': 'Unter den Linden 1',
            'city': 'Berlin', 'country_id': cls.de.id, 'state_id': cls.env['res.country.state'].search(
                [('country_id', '=', cls.de.id)], limit=1).id,
            'nc_foreign_tax_id': '4000001000005', 'nc_foreign_tax_scheme': '0088',
        })

    def _line(self, tax=None):
        return {'product_id': self.product.id, 'quantity': 1, 'price_unit': 100.0,
                'tax_ids': [Command.set((tax or self.tax_s).ids)]}

    def _prepared(self, partner, **vals):
        record = self._einvoice(self._invoice(partner=partner, lines=[self._line()]), **vals)
        record.action_prepare()
        return record

    def test_a_foreign_buyer_is_identified_by_its_foreign_identifier(self):
        record = self._prepared(self.foreigner)
        self.assertEqual(record.local_state, 'ready', sorted(self.rules(record)))
        self.assertEqual((record.buyer_endpoint_id, record.buyer_endpoint_source),
                         ('9900000098', 'predefined_foreign_buyer'))
        record.action_generate_xml()
        xml = record.xml_attachment_id.raw
        customer = xml.split(b'<cac:AccountingCustomerParty>')[1].split(b'</cac:AccountingCustomerParty>')[0]
        self.assertIn(b'<cac:PartyIdentification>', customer)
        self.assertIn(b'schemeID="0088">4000001000005<', customer)
        self.assertNotIn(b'<cac:PartyTaxScheme>', customer, 'IBT-048 is not populated')
        folder = os.environ.get(EXPORT_ENV)
        if folder:
            case = 'real-foreign-buyer-380'
            kind, expect, rule = REQUIRED_CASES[case][:3]
            with open(os.path.join(folder, case + '.xml'), 'wb') as handle:
                handle.write(xml)
            with open(os.path.join(folder, case + '.json'), 'w', encoding='utf-8') as handle:
                json.dump({'case': case, 'kind': kind, 'expect': expect, 'rule': rule}, handle)

    def test_a_foreign_buyer_without_an_identifier_is_blocked_by_name(self):
        nobody = self.foreigner.copy({'name': 'Anonymous GmbH', 'nc_foreign_tax_id': False,
                                      'nc_foreign_tax_scheme': False})
        record = self._prepared(nobody)
        self.assertEqual(record.local_state, 'draft')
        self.assertIn('NCOLLECTION:buyer-identity-missing', self.rules(record))

    def test_a_scheme_outside_the_icd_list_is_refused(self):
        self.assertNotIn('9999', codelists.icd_codes())
        self.assertIn('0088', codelists.icd_codes())
        odd = self.foreigner.copy({'name': 'Odd Scheme GmbH', 'nc_foreign_tax_id': 'X-1',
                                   'nc_foreign_tax_scheme': '9999'})
        self.assertIn('OFFICIAL:ibr-cl-10', self.rules(self._prepared(odd)))

    def test_a_uae_buyer_is_never_treated_as_foreign(self):
        record = self._prepared(self.buyer)
        self.assertEqual(record.buyer_endpoint_source, 'buyer_tin')

    def test_master_data_shape(self):
        for values in ({'nc_foreign_tax_id': 'X'}, {'nc_foreign_tax_scheme': '0088'},
                       {'nc_foreign_tax_id': 'X', 'nc_foreign_tax_scheme': '88'},
                       {'nc_foreign_tax_id': 'X', 'nc_foreign_tax_scheme': '0235'},
                       {'nc_foreign_tax_id': 'has space', 'nc_foreign_tax_scheme': '0088'}):
            with self.assertRaises(ValidationError, msg=values):
                self.env['res.partner'].create(dict({'name': 'Shape', 'country_id': self.de.id}, **values))
