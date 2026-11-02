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
from odoo.exceptions import UserError
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


@tagged('post_install', '-at_install')
class TestCreditNoteThroughOdooReversal(CreditCase):
    """The credit note an accountant actually makes: Odoo's Credit Note wizard, then Confirm. Posting a reversal of a
    posted invoice RECONCILES the two receivables (account ``_post`` → ``_reconcile_reversed_moves``), so the original
    reads 'reversed' (or 'partial') afterwards. What the engine requires of the original is that its E-INVOICE was
    officially validated, never that the accounting invoice is still unpaid: this proves the two never get confused."""

    def _wizard(self, original, **vals):
        return (
            self.env['account.move.reversal']
            .with_context(active_model='account.move', active_ids=original.ids)
            .create(dict({'reason': 'Full cancellation', 'journal_id': original.journal_id.id,
                          'date': date(2026, 10, 5)}, **vals))
        )

    def _reverse(self, original, modify=False):
        wizard = self._wizard(original)
        action = wizard.modify_moves() if modify else wizard.refund_moves()
        return wizard.new_move_ids, action

    def _receivable(self, move):
        return move.line_ids.filtered(lambda line: line.account_type == 'asset_receivable')

    def _single_line_original(self):
        return self._original(lines=[self._line(qty=2)])

    def test_reverse_leaves_an_unreconciled_draft_and_confirm_reconciles_it_in_full(self):
        original, _record = self._single_line_original()
        refund, action = self._reverse(original)
        self.assertEqual(action['res_id'], refund.id)
        self.assertEqual((refund.move_type, refund.state, refund.reversed_entry_id), ('out_refund', 'draft', original))
        self.assertEqual(original.payment_state, 'not_paid', "the wizard alone reconciles nothing")
        self.assertFalse(self._receivable(original).matched_credit_ids)

        refund.action_post()
        partial = self._receivable(original).matched_credit_ids
        self.assertEqual(partial.credit_move_id, self._receivable(refund), "only the two receivables are matched")
        self.assertEqual(partial.amount, 210.0)
        self.assertEqual((original.payment_state, original.amount_residual), ('reversed', 0.0))
        self.assertEqual((refund.payment_state, refund.amount_total_signed), ('paid', -210.0))
        self.assertFalse(self.env['account.payment'].search([('partner_id', '=', self.buyer.id)]),
                         "a reversal reconciles moves, it creates no payment")

        record, xml = self._ready_xml(refund, credit_reason='DL8.61.1.A')
        self.assertEqual((record.document_kind, record.type_code), ('credit_note', '381'))
        self.assertNotIn('NCOLLECTION:credit-note-original-not_einvoiced', self.rules(record))
        self.assertIn(b'<CreditNote ', xml)
        self.assertIn(b'<cbc:ResponseCode>DL8.61.1.A</cbc:ResponseCode>', xml)
        self.assertIn(('<cac:InvoiceDocumentReference><cbc:ID>%s</cbc:ID>' % original.name).encode(),
                      re.sub(rb'>\s+<', b'><', xml))
        self.assertIn(b'<cbc:CreditedQuantity unitCode="C62">2</cbc:CreditedQuantity>', xml)
        self.assertEqual(self._payable(xml), '210.00', "the credit note states positive amounts")
        self._export('real-credit-note-381-reverse-wizard', xml)

    def test_a_partial_credit_through_the_wizard_reconciles_only_its_own_amount(self):
        original, _record = self._single_line_original()
        refund, _action = self._reverse(original)
        refund.invoice_line_ids.filtered(lambda line: line.display_type == 'product').quantity = 1
        refund.action_post()
        self.assertEqual(self._receivable(original).matched_credit_ids.amount, 105.0)
        self.assertEqual((original.payment_state, original.amount_residual), ('partial', 105.0))
        record, xml = self._ready_xml(refund, credit_reason='DL8.61.1.D')
        self.assertEqual(record.type_code, '381')
        self.assertIn(b'<cbc:CreditedQuantity unitCode="C62">1</cbc:CreditedQuantity>', xml)
        self.assertEqual(self._payable(xml), '105.00')

    def test_reverse_and_create_invoice_still_yields_a_valid_credit_note(self):
        original, _record = self._single_line_original()
        # in 'modify' mode the wizard redirects to the NEW draft invoice; the refund is the original's reversal
        replacement, _action = self._reverse(original, modify=True)
        refund = original.reversal_move_ids
        self.assertEqual(len(refund), 1)
        self.assertEqual((refund.move_type, refund.state), ('out_refund', 'posted'),
                         "with no future date, Odoo posts and reconciles the reversal of a cancelling 'modify'")
        self.assertEqual(original.payment_state, 'reversed')
        self.assertEqual((replacement.move_type, replacement.state), ('out_invoice', 'draft'),
                         "the correcting invoice is a new draft, outside the e-invoice until posted")
        record, _xml = self._ready_xml(refund, credit_reason='DL8.61.1.A')
        self.assertEqual(record.type_code, '381')

    def test_a_credit_note_of_an_already_paid_invoice_stays_an_open_credit(self):
        original, _record = self._single_line_original()
        self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=original.ids
        ).create({'payment_date': date(2026, 10, 2)})._create_payments()
        self.assertIn(original.payment_state, ('paid', 'in_payment'))
        refund, _action = self._reverse(original)
        refund.action_post()
        self.assertEqual((refund.payment_state, refund.amount_residual), ('not_paid', 210.0),
                         "nothing was left to reconcile: the refund is an outstanding credit")
        record, xml = self._ready_xml(refund, credit_reason='DL8.61.1.A')
        self.assertEqual(record.type_code, '381')
        self.assertEqual(self._payable(xml), '210.00')

    def test_a_validated_credit_note_refuses_a_second_validation_and_a_new_xml(self):
        original, _record = self._single_line_original()
        refund, _action = self._reverse(original)
        refund.action_post()
        record, xml = self._ready_xml(refund, credit_reason='DL8.61.1.A')
        archive, digest = record.xml_attachment_id, record.xml_sha256
        self._validated(record)
        with self.assertRaisesRegex(UserError, 'Generate the XML before validating'):
            record.action_validate()
        with self.assertRaisesRegex(UserError, 'Only a ready e-invoice'):
            record.action_generate_xml()
        self.assertEqual((record.xml_attachment_id, record.xml_sha256, record.xml_attachment_id.raw),
                         (archive, digest, xml))
        self.assertEqual(record.local_state, 'validated')
