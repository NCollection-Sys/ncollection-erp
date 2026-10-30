# -*- coding: utf-8 -*-
"""Real ``account.move`` → the engine's own mapper → XML, exported for the OFFICIAL PINT-AE rules.

The mapper (models/prepare.py, on real Odoo documents) and the official validator (satellites/einvoice_validator) were
each proven on their own; nothing proved that what the mapper produces from a real posted invoice is accepted by the
official rules. These tests close that gap: every case builds a real posted document, runs the real preparation and
XML generation, and asserts the document reached XML. When ``NC_EINVOICE_EXPORT_DIR`` is set (CI and ``make
einvoice-e2e EINVOICE_FIXTURES=<dir>``), each case also writes ``<case>.xml`` and ``<case>.json`` (kind, expected
outcome, expected rule) there; ``e2e_check.py`` then validates them under the official rules and FAILS if any required
case is missing, so an export that silently produced nothing cannot pass.

The list of cases is :data:`REQUIRED_CASES`, shared with e2e_check.py (which imports this module's constant through
its own path, without Odoo)."""

import json
import os
import re
from datetime import date

from odoo import Command
from odoo.tests import tagged

from .common import EInvoiceCase

EXPORT_ENV = 'NC_EINVOICE_EXPORT_DIR'

# case -> (kind, expected official outcome, rule that must fire for an expected failure[, profile key])
REQUIRED_CASES = {
    'real-tax-invoice-380': ('invoice', 'pass', None),
    'real-tax-credit-note-381': ('credit_note', 'pass', None),
    'real-out-of-scope-commercial-480': ('invoice', 'pass', None),
    'real-exempt-only-480': ('invoice', 'pass', None),
    'real-zero-rated-380': ('invoice', 'pass', None),
    'real-reverse-charge-380': ('invoice', 'pass', None),
    'real-export-scenario-380': ('invoice', 'pass', None),
    'real-negative-uuid-removed': ('invoice', 'fail', 'ibr-193-ae'),
    # tests/test_credit_notes.py
    'real-credit-note-381-full': ('credit_note', 'pass', None),
    'real-credit-note-381-partial-line': ('credit_note', 'pass', None),
    'real-credit-note-381-partial-multi': ('credit_note', 'pass', None),
    'real-commercial-credit-note-81': ('credit_note', 'pass', None),
    # tests/test_scenario_matrix.py
    'real-scenario-free-zone': ('invoice', 'pass', None),
    'real-scenario-deemed-supply': ('invoice', 'pass', None),
    'real-scenario-margin-scheme': ('invoice', 'pass', None),
    'real-scenario-summary': ('invoice', 'pass', None),
    'real-scenario-continuous-supply': ('invoice', 'pass', None),
    'real-scenario-agent-billing': ('invoice', 'pass', None),
    'real-scenario-ecommerce': ('invoice', 'pass', None),
    'real-scenario-free-zone-ecommerce': ('invoice', 'pass', None),
    'real-scenario-summary-continuous': ('invoice', 'pass', None),
    # tests/test_self_billing.py: validated by the Self-Billing worker (the 4th element names the profile)
    'real-self-billed-tax-invoice-389': ('invoice', 'pass', None, 'pint-ae-sb-1.0.4'),
    'real-self-billed-tax-credit-note-261': ('credit_note', 'pass', None, 'pint-ae-sb-1.0.4'),
    # tests/test_foreign_buyer.py (R05, Guidelines V2.0 §18.2.3)
    'real-foreign-buyer-380': ('invoice', 'pass', None),
}


@tagged('post_install', '-at_install')
class TestRealMoveCrossCheck(EInvoiceCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Tax, Map = cls.env['account.tax'], cls.env['ncollection.einvoice.tax.map']
        cls.tax_oos = Tax.create(
            {
                'name': 'Out of scope 0% (crosscheck)',
                'amount': 0.0,
                'type_tax_use': 'sale',
                'company_id': cls.company.id,
            }
        )
        Map.create({'company_id': cls.company.id, 'tax_id': cls.tax_oos.id, 'pint_category': 'O'})
        cls.tax_rc = Tax.create(
            {'name': 'RC 0% (crosscheck)', 'amount': 0.0, 'type_tax_use': 'sale', 'company_id': cls.company.id}
        )
        Map.create(
            {
                'company_id': cls.company.id,
                'tax_id': cls.tax_rc.id,
                'pint_category': 'AE',
                'rcm_goods_type': 'DL8.48.8.2',
                'pint_rate': 5.0,
                'rc_narrative': 'Reverse charge: Cabinet Decision No. 91 of 2023 (electronic devices)',
            }
        )
        cls.device = cls.env['product.product'].create(
            {'name': 'Smartphone', 'nc_item_type': 'G', 'nc_hs_code': '85171300', 'barcode': '4006381333931'}
        )

    # ---------------------------------------------------------------- helpers
    def _line(self, tax, product=None, price=100.0, qty=1):
        return {
            'product_id': (product or self.product).id,
            'quantity': qty,
            'price_unit': price,
            'tax_ids': [Command.set(tax.ids)],
        }

    def _xml_of(self, move, **vals):
        record = self._einvoice(move, **vals)
        record.action_prepare()
        self.assertEqual(record.local_state, 'ready', sorted(self.rules(record)))
        record.action_generate_xml()
        self.assertEqual(record.local_state, 'locally_checked')
        return record, record.xml_attachment_id.raw

    def _export(self, case, xml):
        kind, expect, rule = REQUIRED_CASES[case][:3]
        self.assertTrue(xml.startswith(b'<?xml'), case)
        folder = os.environ.get(EXPORT_ENV)
        if folder:
            with open(os.path.join(folder, case + '.xml'), 'wb') as handle:
                handle.write(xml)
            with open(os.path.join(folder, case + '.json'), 'w', encoding='utf-8') as handle:
                json.dump({'case': case, 'kind': kind, 'expect': expect, 'rule': rule}, handle)

    # ---------------------------------------------------------------- the cases
    def test_real_tax_invoice_380(self):
        record, xml = self._xml_of(self._invoice())
        self.assertEqual(record.type_code, '380')
        self._export('real-tax-invoice-380', xml)

    def test_real_tax_credit_note_381(self):
        original = self._invoice(invoice_date=date(2026, 10, 1), invoice_date_due=date(2026, 10, 31))
        refund = self._invoice(
            move_type='out_refund',
            reversed_entry_id=original.id,
            invoice_date=date(2026, 10, 5),
            invoice_date_due=date(2026, 10, 5),
        )
        record, xml = self._xml_of(refund, credit_reason='DL8.61.1.E')
        self.assertEqual(record.type_code, '381')
        self._export('real-tax-credit-note-381', xml)

    def test_real_out_of_scope_commercial_480(self):
        record, xml = self._xml_of(self._invoice(lines=[self._line(self.tax_oos)]))
        self.assertEqual(record.type_code, '480')
        self._export('real-out-of-scope-commercial-480', xml)

    def test_real_exempt_only_480(self):
        # DECISION 2026-10-28 / OCQ-20: exempt-only is 480 because fatal ibr-151-ae refuses it as 380
        record, xml = self._xml_of(self._invoice(lines=[self._line(self.tax_ext)]))
        self.assertEqual(record.type_code, '480')
        self._export('real-exempt-only-480', xml)

    def test_real_zero_rated_380(self):
        record, xml = self._xml_of(self._invoice(lines=[self._line(self.tax_z)]))
        self.assertEqual(record.type_code, '380')
        self._export('real-zero-rated-380', xml)

    def test_real_reverse_charge_380(self):
        record, xml = self._xml_of(self._invoice(lines=[self._line(self.tax_rc, product=self.device, price=1500.0)]))
        self.assertIn(b'<cbc:TaxExemptionReason>Reverse charge', xml)
        self._export('real-reverse-charge-380', xml)

    def test_real_export_scenario_380(self):
        us = self.env.ref('base.us')
        ny = self.env['res.country.state'].search([('country_id', '=', us.id), ('code', '=', 'NY')], limit=1)
        buyer = self.env['res.partner'].create(
            {'name': 'Gotham Imports Inc', 'company_type': 'company', 'street': '5th Avenue', 'city': 'New York',
             'state_id': ny.id, 'country_id': us.id}
        )
        record, xml = self._xml_of(self._invoice(partner=buyer, lines=[self._line(self.tax_z)]), flag_export=True)
        self.assertEqual(record.transaction_type_code, '00000001')
        self.assertIn(b'<cac:Delivery>', xml)
        self._export('real-export-scenario-380', xml)

    def test_real_negative_uuid_removed(self):
        """The negative control on a REAL document: the same mapper output with its UUID removed must be refused by
        the official rules (ibr-193-ae), so the cross-check can tell a defective XML from a good one."""
        _record, xml = self._xml_of(self._invoice())
        broken = re.sub(rb'<cbc:UUID>[^<]*</cbc:UUID>\s*', b'', xml, count=1)
        self.assertNotEqual(broken, xml)
        self._export('real-negative-uuid-removed', broken)
