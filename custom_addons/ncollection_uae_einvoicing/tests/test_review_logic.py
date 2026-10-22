# -*- coding: utf-8 -*-
"""Defects found by the pre-merge code review, each pinned by the input that exposed it."""

from decimal import Decimal as D

from lxml import etree

from odoo.tests import TransactionCase, tagged

from ..lib import adjustments, identity, readiness, reconcile, spec
from ..lib.xml_builder import NS_CAC, NS_CBC, build
from . import payloads
from .test_pint_logic import rules, snapshot

NS = {'cac': NS_CAC, 'cbc': NS_CBC}


@tagged('post_install', '-at_install')
class TestReconciliationWithUnmappedLines(TransactionCase):

    def test_a_document_mixing_a_taxless_line_with_a_taxed_one_is_reported_not_a_traceback(self):
        """prepare.py gives ``category=None`` to a line with no tax or an unmapped tax; sorting the VAT groups then
        compared None with 'S' and raised TypeError instead of letting the readiness rules block the document."""
        lines = [payloads._line(1, 1, '100.00'), payloads._line(2, 1, '10.00', category=None, rate=None)]
        lines[1]['rate'] = None
        payload = payloads.standard_payload(lines=lines, odoo_totals=None)
        result = reconcile.reconcile(payload)
        self.assertTrue(result.checks, "the groups are reconciled, the unmapped one included")

    def test_two_unmapped_and_two_mapped_groups_in_any_order(self):
        lines = [
            payloads._line(1, 1, '10.00', category=None, rate=None),
            payloads._line(2, 1, '20.00', rate='0', category='Z'),
            payloads._line(3, 1, '30.00'),
            payloads._line(4, 1, '5.00', category='E', exemption_code='DL8.46.1'),
        ]
        reconcile.reconcile(payloads.standard_payload(lines=lines, odoo_totals=None))


@tagged('post_install', '-at_install')
class TestExemptAllowanceCarriesPercentZero(TransactionCase):

    def _document_adjustment(self, category, exemption=None):
        _items, adjs, found = adjustments.split_lines(
            [
                {
                    'id': '2',
                    'name': 'Rebate',
                    'qty': D('1'),
                    'price_unit': D('-10'),
                    'net': D('-10'),
                    'category': category,
                    'rate': None if category in ('E', 'O') else D('0'),
                    'exemption_code': exemption,
                    'odoo_tax': D('0'),
                    'adjustment': 'allowance',
                    'reason_code': '95',
                    'reason': 'Discount',
                }
            ]
        )
        self.assertFalse(found, found)
        payload = payloads.standard_payload(
            type_code='480',
            lines=[payloads._line(1, 1, '100.00', category=category, exemption_code=exemption)],
            adjustments=adjs,
            odoo_totals=None,
        )
        root = etree.fromstring(build(payload))
        return root.findall('cac:AllowanceCharge/cac:TaxCategory', NS)

    def test_aligned_ibrp_e_06_a_document_allowance_in_category_e_has_percent_0(self):
        (category,) = self._document_adjustment('E', 'DL8.46.1')
        self.assertEqual(category.findtext('cbc:ID', namespaces=NS), 'E')
        self.assertEqual(category.findtext('cbc:Percent', namespaces=NS), '0')

    def test_aligned_ibrp_o_06_category_o_must_not_carry_a_percent(self):
        (category,) = self._document_adjustment('O')
        self.assertEqual(category.findtext('cbc:ID', namespaces=NS), 'O')
        self.assertIsNone(category.find('cbc:Percent', NS))

    def test_the_rule_the_builder_follows_is_in_the_pinned_schematron(self):
        path = spec.get_profile().schematron_dir + '/PINT-jurisdiction-aligned-rules.sch'
        with open(path, encoding='utf-8') as handle:
            text = handle.read()
        for rule_id in ('aligned-ibrp-e-06', 'aligned-ibrp-e-07', 'aligned-ibrp-o-06', 'aligned-ibrp-o-07'):
            self.assertIn('id="%s"' % rule_id, text)


@tagged('post_install', '-at_install')
class TestVolumeDiscountCreditNote(TransactionCase):

    def test_ibr_055_ae_a_volume_discount_credit_note_must_not_reference_an_invoice(self):
        snap = snapshot()
        snap['doc'].update(
            kind='credit_note', type_code='381', due_date=None, credit_reason='VD', preceding=[{'number': 'INV/1'}]
        )
        self.assertIn('OFFICIAL:ibr-055-ae', rules(readiness.check(snap)))

    def test_the_other_reasons_still_need_the_reference(self):
        snap = snapshot()
        snap['doc'].update(
            kind='credit_note', type_code='381', due_date=None, credit_reason='DL8.61.1.A', preceding=[]
        )
        self.assertIn('OFFICIAL:ibr-055-ae', rules(readiness.check(snap)))
        snap['doc'].update(preceding=[{'number': 'INV/1'}])
        self.assertNotIn('OFFICIAL:ibr-055-ae', rules(readiness.check(snap)))

    def test_vd_without_a_reference_is_accepted(self):
        snap = snapshot()
        snap['doc'].update(kind='credit_note', type_code='381', due_date=None, credit_reason='VD', preceding=[])
        self.assertNotIn('OFFICIAL:ibr-055-ae', rules(readiness.check(snap)))


@tagged('post_install', '-at_install')
class TestGtin(TransactionCase):

    def test_only_a_real_gtin_with_a_correct_check_digit_is_a_gtin(self):
        for good in ('4006381333931', '96385074', '036000291452', '10012345678902'):
            self.assertTrue(identity.valid_gtin(good), good)
        for bad in (
            '4006381333932',
            '12345',
            '',
            None,
            'ABCDEFGH',
            '４００６３８１３３３９３１',
            '40063813339311',
            'INTERNAL-001',
        ):
            self.assertFalse(identity.valid_gtin(bad), bad)
