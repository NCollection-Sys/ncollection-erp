# -*- coding: utf-8 -*-
"""Electronic credit notes on REAL posted documents: tax (381), commercial (81) and their original-invoice reference.

The reference is Odoo's own reversal link (``reversed_entry_id``), never a typed value, so it cannot be ambiguous or
point outside this database. What the engine checks about it (models/prepare.py ``_nc_original_facts`` → facts,
lib/readiness.py ``_original`` → findings): same company, same direction (a customer credit note corrects a customer
invoice), same counterparty, posted, its own e-invoice officially validated with the corresponding type (381 ↔ 380,
81 ↔ 480, 261 ↔ 389), and every posted credit note of that original together crediting no more amount and no more VAT
than it carried (VAT Decree-Law Art 70). Guidelines V2.0 §12.2 #4: a credit note may cover part of the invoice."""

import json
import os
import re
from datetime import date

from odoo import Command
from odoo.tests import tagged
from odoo.tools import mute_logger

from .common import EInvoiceCase
from .test_real_move_crosscheck import EXPORT_ENV, REQUIRED_CASES

REASON = 'DL8.61.1.E'


class CreditCase(EInvoiceCase):

    def _line(self, tax=None, qty=1, price=100.0):
        return {'product_id': self.product.id, 'quantity': qty, 'price_unit': price,
                'tax_ids': [Command.set((tax or self.tax_s).ids)]}

    def _original(self, lines=None, validated=True):
        move = self._invoice(lines=lines or [self._line(qty=2), self._line(self.tax_z, qty=1, price=50.0)],
                             invoice_date=date(2026, 10, 1), invoice_date_due=date(2026, 10, 31))
        record = self._einvoice(move)
        record.action_prepare()
        self.assertEqual(record.local_state, 'ready', sorted(self.rules(record)))
        if validated:
            self._validated(record)
        return move, record

    def _refund(self, original, lines, post=True):
        return self._invoice(move_type='out_refund', reversed_entry_id=original.id, lines=lines,
                             invoice_date=date(2026, 10, 5), invoice_date_due=date(2026, 10, 5), post=post)

    def _prepared(self, move, **vals):
        record = self._einvoice(move, **dict({'credit_reason': REASON}, **vals))
        record.action_prepare()
        return record

    def _ready_xml(self, move, **vals):
        record = self._prepared(move, **vals)
        self.assertEqual(record.local_state, 'ready', sorted(self.rules(record)))
        record.action_generate_xml()
        return record, record.xml_attachment_id.raw

    def _export(self, case, xml):
        folder = os.environ.get(EXPORT_ENV)
        if folder:
            kind, expect, rule = REQUIRED_CASES[case][:3]
            with open(os.path.join(folder, case + '.xml'), 'wb') as handle:
                handle.write(xml)
            with open(os.path.join(folder, case + '.json'), 'w', encoding='utf-8') as handle:
                json.dump({'case': case, 'kind': kind, 'expect': expect, 'rule': rule}, handle)

    @staticmethod
    def _payload(record):
        return json.loads(record.payload_json)

    @staticmethod
    def _payable(xml):
        return re.search(rb'<cbc:PayableAmount currencyID="AED">([0-9.]+)<', xml).group(1).decode()


@tagged('post_install', '-at_install')
class TestCreditNoteAmounts(CreditCase):

    def test_a_full_reversal(self):
        original, _record = self._original()
        refund = self._refund(original, [self._line(qty=2), self._line(self.tax_z, qty=1, price=50.0)])
        record, xml = self._ready_xml(refund)
        self.assertEqual(record.type_code, '381')
        self.assertEqual((refund.amount_untaxed, refund.amount_tax, refund.amount_total),
                         (original.amount_untaxed, original.amount_tax, original.amount_total))
        self.assertEqual(float(self._payable(xml)), 260.0)
        self.assertIn(('<cbc:ID>%s</cbc:ID>' % original.name).encode(), xml)
        self._export('real-credit-note-381-full', xml)

    def test_a_one_line_partial_credit(self):
        original, _record = self._original()
        record, xml = self._ready_xml(self._refund(original, [self._line(self.tax_z, qty=1, price=50.0)]))
        self.assertEqual(float(self._payable(xml)), 50.0)
        self.assertEqual(record.type_code, '381', 'a zero-rated credit is still a tax credit note')
        self._export('real-credit-note-381-partial-line', xml)

    def test_a_multi_line_partial_quantity_credit(self):
        original, _record = self._original()
        record, xml = self._ready_xml(self._refund(
            original, [self._line(qty=1), self._line(self.tax_z, qty=1, price=20.0)]))
        self.assertEqual(float(self._payable(xml)), 125.0)  # 100 + 5 VAT + 20 zero-rated
        self.assertEqual(len(self._payload(record)['lines']), 2)
        self._export('real-credit-note-381-partial-multi', xml)

    def test_a_partial_amount_credit(self):
        original, _record = self._original()
        _record, xml = self._ready_xml(self._refund(original, [self._line(qty=1, price=30.0)]))
        self.assertEqual(float(self._payable(xml)), 31.5)

    def test_a_partial_tax_adjustment(self):
        """Only the standard-rated part is credited: the credit carries VAT on that part only."""
        original, _record = self._original()
        refund = self._refund(original, [self._line(qty=2)])
        record, _xml = self._ready_xml(refund)
        self.assertEqual(refund.amount_tax, original.amount_tax, 'all the VAT, none of the zero-rated amount')
        self.assertLess(refund.amount_total, original.amount_total)
        self.assertEqual(record.reconciliation_state, 'exact')

    def test_credits_beyond_the_original_amount_are_refused(self):
        original, _record = self._original()
        first = self._refund(original, [self._line(qty=2)])
        self.assertEqual(self._prepared(first).local_state, 'ready')
        second = self._refund(original, [self._line(self.tax_z, qty=2, price=50.0)])  # 50 more than remains
        record = self._prepared(second)
        self.assertEqual(record.local_state, 'draft')
        self.assertIn('NCOLLECTION:credit-exceeds-original', self.rules(record))

    def test_credited_vat_beyond_the_original_vat_is_refused(self):
        original, _record = self._original(lines=[self._line(self.tax_z, qty=2)])
        # same amount, but VAT that the zero-rated original never charged
        refund = self._refund(original, [self._line(qty=1, price=190.0)])
        record = self._prepared(refund)
        self.assertIn('NCOLLECTION:credit-exceeds-original', self.rules(record))

    def test_a_commercial_credit_note_81_for_a_480(self):
        original, record = self._original(lines=[self._line(self.tax_ext)])
        self.assertEqual(record.type_code, '480')
        credit, xml = self._ready_xml(self._refund(original, [self._line(self.tax_ext, price=40.0)]))
        self.assertEqual(credit.type_code, '81')
        self._export('real-commercial-credit-note-81', xml)

    def test_a_volume_discount_credit_note_needs_no_original(self):
        refund = self._invoice(move_type='out_refund', lines=[self._line(price=10.0)])
        record, _xml = self._ready_xml(refund, credit_reason='VD')
        self.assertEqual(record.type_code, '381')


@tagged('post_install', '-at_install')
class TestCreditNoteReferences(CreditCase):

    def _link(self, refund, original):
        """Point the reversal link somewhere the UI never would (a direct database write), to prove the engine
        checks the link itself rather than trusting how it was made."""
        self.env.cr.execute('UPDATE account_move SET reversed_entry_id = %s WHERE id = %s', (original.id, refund.id))
        refund.invalidate_recordset(['reversed_entry_id'])

    def test_a_tax_credit_note_without_an_original_is_refused(self):
        record = self._prepared(self._invoice(move_type='out_refund', lines=[self._line()]))
        self.assertIn('OFFICIAL:ibr-055-ae', self.rules(record))
        self.assertEqual(record.local_state, 'draft')

    def test_an_original_of_another_company_is_refused(self):
        other = self._company('Other Co', tin='1876543210', trn='187654321002003', registry='112345678900099')
        foreign = self.env['account.move'].with_company(other).create({
            'move_type': 'out_invoice', 'partner_id': self.buyer.id, 'company_id': other.id,
            'invoice_date': date(2026, 10, 1),
            'invoice_line_ids': [Command.create({'name': 'x', 'quantity': 1, 'price_unit': 100.0, 'tax_ids': []})],
        })
        foreign.action_post()
        refund = self._invoice(move_type='out_refund', lines=[self._line()])
        self._link(refund, foreign)
        self.assertIn('NCOLLECTION:credit-note-original-other_company', self.rules(self._prepared(refund)))

    def test_an_original_of_another_customer_is_refused(self):
        stranger = self._partner('Stranger LLC', tin='1222333444', trn='122233344455003', state='DU')
        original = self._invoice(partner=stranger)
        refund = self._invoice(move_type='out_refund', lines=[self._line()])
        self._link(refund, original)
        self.assertIn('NCOLLECTION:credit-note-original-other_partner', self.rules(self._prepared(refund)))

    def test_an_original_that_is_not_a_customer_invoice_is_refused(self):
        bill = self._invoice(move_type='in_invoice', lines=[
            {'name': 'x', 'quantity': 1, 'price_unit': 10.0, 'tax_ids': []}])
        refund = self._invoice(move_type='out_refund', lines=[self._line()])
        self._link(refund, bill)
        self.assertIn('NCOLLECTION:credit-note-original-wrong_kind', self.rules(self._prepared(refund)))

    def test_an_original_that_is_no_longer_posted_is_refused(self):
        original, _record = self._original(validated=False)
        refund = self._refund(original, [self._line()])
        original.button_draft()
        self.assertIn('NCOLLECTION:credit-note-original-not_posted', self.rules(self._prepared(refund)))

    def test_an_original_whose_einvoice_never_validated_is_refused(self):
        original, _record = self._original(validated=False)
        record = self._prepared(self._refund(original, [self._line()]))
        self.assertIn('NCOLLECTION:credit-note-original-not_einvoiced', self.rules(record))

    def test_a_tax_credit_note_for_a_commercial_invoice_is_refused(self):
        original, _record = self._original(lines=[self._line(self.tax_ext)])  # 480
        record = self._prepared(self._refund(original, [self._line()]))  # standard-rated: 381
        self.assertIn('NCOLLECTION:credit-note-original-type_mismatch', self.rules(record))

    def test_an_original_without_any_einvoice_is_only_flagged(self):
        original = self._invoice()
        record = self._prepared(self._refund(original, [self._line()]))
        self.assertEqual(record.local_state, 'ready')
        warning = record.validation_ids.filtered(
            lambda v: v.rule == 'NCOLLECTION:credit-note-original-without-einvoice')
        self.assertEqual(warning.severity, 'warning')

    def test_an_original_from_before_the_mandate_is_not_flagged(self):
        self.config.sudo().write({'effective_date': date(2026, 10, 2)})
        original = self._invoice(invoice_date=date(2026, 10, 1), invoice_date_due=date(2026, 10, 31))
        record = self._prepared(self._refund(original, [self._line()]))
        self.assertNotIn('NCOLLECTION:credit-note-original-without-einvoice', self.rules(record))

    def test_a_credit_note_has_one_einvoice_per_revision(self):
        original, _record = self._original()
        refund = self._refund(original, [self._line()])
        self._prepared(refund)
        with mute_logger('odoo.sql_db'), self.assertRaises(Exception), self.env.cr.savepoint():
            self._einvoice(refund)

    def test_a_credit_note_uses_the_same_lifecycle(self):
        """Draft → prepared → XML → validated → submittable: no credit-note shortcut past the engine."""
        original, _record = self._original()
        record, _xml = self._ready_xml(self._refund(original, [self._line()]))
        self.assertEqual(record.local_state, 'locally_checked')
        self.assertEqual(record.submission_state, 'not_submitted')
        self.assertFalse(record.locked)
        steps = record.history_ids.filtered(lambda h: h.dimension == 'local_state').mapped('to_state')
        self.assertEqual(steps[-2:], ['ready', 'locally_checked'])
