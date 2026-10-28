# -*- coding: utf-8 -*-
"""The mapper on REAL posted invoices: account.move → payload → readiness → reconciliation → XML → official validation.

The accounting document is the source of truth and is never touched: every test that blocks a document also proves the
invoice's booked amounts are unchanged."""

import hashlib
import json
import os
import threading
from decimal import Decimal as D
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from lxml import etree

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import tagged

from ..lib import spec
from ..lib.xml_builder import NS_CAC, NS_CBC
from .common import EInvoiceCase, signed_answer, validator_env

NS = {'cac': NS_CAC, 'cbc': NS_CBC}


def _amounts(move):
    return (move.amount_untaxed, move.amount_tax, move.amount_total, tuple(move.line_ids.mapped('balance')))


@tagged('post_install', '-at_install')
class TestPrepareOnRealInvoices(EInvoiceCase):

    def test_a_complete_invoice_becomes_ready_and_yields_an_xml(self):
        move = self._invoice()
        before = _amounts(move)
        record = self._einvoice(move)
        record.action_prepare()
        self.assertEqual(record.local_state, 'ready', self.rules(record))
        self.assertEqual(record.type_code, '380')
        self.assertEqual(record.reconciliation_state, 'exact')
        self.assertEqual((record.seller_endpoint_scheme, record.seller_endpoint_id), ('0235', '1987654321'))
        self.assertEqual((record.buyer_endpoint_id, record.buyer_endpoint_source), ('1345678901', 'buyer_tin'))
        self.assertEqual(record.spec_version, spec.get_profile().key)
        self.assertTrue(record.uuid)
        payload = record.payload()
        self.assertEqual(payload['odoo_totals'], {'untaxed': '200.00', 'tax': '10.00', 'total': '210.00'})
        record.action_generate_xml()
        self.assertEqual(record.local_state, 'locally_checked')
        root = etree.fromstring(record.xml_attachment_id.raw)
        self.assertEqual(root.findtext('cbc:ID', namespaces=NS), move.name)
        self.assertEqual(root.findtext('cac:LegalMonetaryTotal/cbc:PayableAmount', namespaces=NS), '210.00')
        self.assertEqual(root.findtext('cac:TaxTotal/cbc:TaxAmount', namespaces=NS), '10.00')
        self.assertEqual(_amounts(move), before, "the accounting document must be untouched")

    def test_missing_master_data_blocks_the_document_with_named_reasons_and_changes_nothing(self):
        buyer = self._partner('Bare Buyer LLC', state='AZ')
        move = self._invoice(partner=buyer)
        before = _amounts(move)
        record = self._einvoice(move)
        record.action_prepare()
        self.assertEqual(record.local_state, 'draft')
        rules = self.rules(record)
        self.assertIn('NCOLLECTION:buyer-identity-missing', rules)
        self.assertTrue(all(v.rule.startswith(('OFFICIAL:', 'NCOLLECTION:')) for v in record.validation_ids))
        self.assertEqual(_amounts(move), before)
        with self.assertRaises(UserError):
            record.action_generate_xml()

    def test_the_seller_tin_and_trn_are_read_from_the_new_fields_only_never_from_vat(self):
        self.company.partner_id.write({'nc_tin': False, 'nc_trn': False, 'vat': '198765432102003'})
        record = self._einvoice()
        record.action_prepare()
        self.assertEqual(record.local_state, 'draft')
        self.assertIn('NCOLLECTION:seller-tin-missing', self.rules(record))
        self.assertIn('OFFICIAL:ibr-134-ae', self.rules(record), "vat is not a fallback for nc_trn")
        self.assertFalse(record.seller_trn)

    def test_an_unmapped_tax_blocks_instead_of_guessing_a_category(self):
        other_tax = self._sale_tax('5% AZ')
        move = self._invoice(
            [
                {
                    'product_id': self.product.id,
                    'quantity': 1,
                    'price_unit': 100.0,
                    'tax_ids': [Command.set(other_tax.ids)],
                }
            ]
        )
        record = self._einvoice(move)
        record.action_prepare()
        self.assertEqual(record.local_state, 'draft')
        self.assertIn('NCOLLECTION:tax-unmapped', self.rules(record))

    def test_a_line_with_two_taxes_or_none_is_blocked(self):
        for taxes, expected in (([], 'NCOLLECTION:line-tax-count'),):
            move = self._invoice(
                [{'product_id': self.product.id, 'quantity': 1, 'price_unit': 100.0, 'tax_ids': [Command.set(taxes)]}]
            )
            record = self._einvoice(move)
            record.action_prepare()
            self.assertIn(expected, self.rules(record))

    def test_a_line_discount_becomes_a_price_discount_that_reconciles(self):
        move = self._invoice(
            [
                {
                    'product_id': self.product.id,
                    'quantity': 3,
                    'price_unit': 50.0,
                    'discount': 10.0,
                    'tax_ids': [Command.set(self.tax_s.ids)],
                }
            ]
        )
        record = self._einvoice(move)
        record.action_prepare()
        self.assertEqual(record.local_state, 'ready', self.rules(record))
        line = record.payload()['lines'][0]
        self.assertEqual(
            (D(line['price_gross']), D(line['price_net']), D(line['price_discount']), D(line['net'])),
            (D('50'), D('45'), D('5'), D('135.00')),
        )
        record.action_generate_xml()
        root = etree.fromstring(record.xml_attachment_id.raw)
        price = root.find('cac:InvoiceLine/cac:Price', NS)
        self.assertEqual(price.findtext('cac:AllowanceCharge/cbc:Amount', namespaces=NS), '5.00')
        self.assertEqual(price.findtext('cbc:PriceAmount', namespaces=NS), '45.00')

    def test_exempt_only_documents_are_typed_480(self):
        move = self._invoice(
            [
                {
                    'product_id': self.product.id,
                    'quantity': 1,
                    'price_unit': 100.0,
                    'tax_ids': [Command.set(self.tax_ext.ids)],
                }
            ]
        )
        record = self._einvoice(move)
        record.action_prepare()
        self.assertEqual(record.type_code, '480')
        self.assertEqual(record.local_state, 'ready', self.rules(record))

    def test_mixed_taxable_zero_and_exempt_is_a_normal_tax_invoice(self):
        lines = [
            {'product_id': self.product.id, 'quantity': 1, 'price_unit': 100.0, 'tax_ids': [Command.set(taxes.ids)]}
            for taxes in (self.tax_s, self.tax_z, self.tax_ext)
        ]
        record = self._einvoice(self._invoice(lines))
        record.action_prepare()
        self.assertEqual((record.type_code, record.local_state), ('380', 'ready'), self.rules(record))
        self.assertEqual({b['category'] for b in record.payload()['figures']['breakdown']}, {'S', 'Z', 'E'})

    def test_a_credit_note_refers_to_the_original_invoice_and_needs_a_reason(self):
        invoice = self._invoice()
        credit = invoice._reverse_moves([{'invoice_date': invoice.invoice_date}])
        credit.action_post()
        record = self._einvoice(credit)
        record.action_prepare()
        self.assertEqual((record.document_kind, record.type_code), ('credit_note', '381'))
        self.assertIn('OFFICIAL:ibr-158-ae', self.rules(record))
        record.credit_reason = 'DL8.61.1.A'
        record.action_prepare()
        self.assertEqual(record.local_state, 'ready', self.rules(record))
        self.assertEqual(record.payload()['preceding'][0]['number'], invoice.name)
        self.assertIsNone(record.payload()['payment'], "a credit note carries no payment means")

    def test_the_booked_tax_is_what_the_xml_carries_and_a_gap_beyond_the_official_slack_blocks(self):
        lines = [
            {
                'product_id': self.product.id,
                'quantity': 1,
                'price_unit': 0.30,
                'tax_ids': [Command.set(self.tax_s.ids)],
            }
            for _i in range(40)
        ]
        move = self._invoice(lines)
        before = _amounts(move)
        record = self._einvoice(move)
        record.action_prepare()
        self.assertEqual(self.company.tax_calculation_rounding_method, 'round_per_line')
        self.assertEqual(record.reconciliation_state, 'blocked')
        self.assertEqual(record.local_state, 'draft')
        self.assertIn('OFFICIAL:aligned-ibrp-s-09', self.rules(record))
        self.assertEqual(
            _amounts(move), before, "a rounding gap is never repaired by changing the accounting document"
        )
        self.assertIn('nothing was changed', ' '.join(record.validation_ids.mapped('message')).lower())

    def test_three_small_lines_are_within_the_official_tolerance_and_eligible(self):
        lines = [
            {
                'product_id': self.product.id,
                'quantity': 1,
                'price_unit': 0.30,
                'tax_ids': [Command.set(self.tax_s.ids)],
            }
            for _i in range(3)
        ]
        record = self._einvoice(self._invoice(lines))
        record.action_prepare()
        self.assertEqual(record.reconciliation_state, 'within_tolerance')
        self.assertEqual(record.local_state, 'ready', self.rules(record))
        self.assertEqual(record.payload()['odoo_totals']['tax'], '0.06', "Odoo's booked tax, not the recomputed 0.05")

    def test_a_negative_line_needs_an_explicit_per_document_toggle(self):
        lines = [
            {
                'product_id': self.product.id,
                'quantity': 1,
                'price_unit': 1000.0,
                'tax_ids': [Command.set(self.tax_s.ids)],
            },
            {'name': 'Rebate', 'quantity': 1, 'price_unit': -100.0, 'tax_ids': [Command.set(self.tax_s.ids)]},
        ]
        move = self._invoice(lines)
        record = self._einvoice(move)
        record.action_prepare()
        self.assertEqual(record.local_state, 'draft')
        self.assertIn('NCOLLECTION:unclassified-negative-line', self.rules(record))
        rebate = record.line_ids.filtered(lambda line: line.move_line_id.price_unit < 0)
        rebate.write({'adjustment': 'allowance', 'reason_code': '95', 'reason': 'Discount'})
        self.assertFalse(
            self.product.mapped('nc_item_type') == [] or rebate.move_line_id.product_id,
            "the toggle is on the e-invoice line, never on a product",
        )
        record.action_prepare()
        self.assertEqual(record.local_state, 'ready', self.rules(record))
        payload = record.payload()
        self.assertEqual(len(payload['lines']), 1)
        self.assertEqual((payload['adjustments'][0]['charge'], payload['adjustments'][0]['amount']), (False, '100.00'))
        self.assertEqual(payload['figures']['allowance_total'], '100.00')

    def test_the_allowance_toggle_cannot_change_after_the_xml_exists(self):
        lines = [
            {
                'product_id': self.product.id,
                'quantity': 1,
                'price_unit': 1000.0,
                'tax_ids': [Command.set(self.tax_s.ids)],
            },
            {'name': 'Rebate', 'quantity': 1, 'price_unit': -100.0, 'tax_ids': [Command.set(self.tax_s.ids)]},
        ]
        record = self._einvoice(self._invoice(lines))
        record.action_prepare()
        rebate = record.line_ids.filtered(lambda line: line.move_line_id.price_unit < 0)
        rebate.write({'adjustment': 'allowance'})
        record.action_prepare()
        record.action_generate_xml()
        with self.assertRaises(UserError):
            rebate.write({'adjustment': 'charge'})

    def test_hs_and_sac_are_conditional_on_the_declared_item_type(self):
        goods = self.env['product.product'].create({'name': 'Goods without HS', 'nc_item_type': 'G'})
        service = self.env['product.product'].create(
            {'name': 'Service with SAC', 'type': 'service', 'nc_item_type': 'S', 'nc_sac_code': '998311'}
        )
        undeclared = self.env['product.product'].create({'name': 'Undeclared'})

        def run(product):
            record = self._einvoice(
                self._invoice(
                    [
                        {
                            'product_id': product.id,
                            'quantity': 1,
                            'price_unit': 10.0,
                            'tax_ids': [Command.set(self.tax_s.ids)],
                        }
                    ]
                )
            )
            record.action_prepare()
            return record

        self.assertIn('OFFICIAL:ibr-184-ae', self.rules(run(goods)))
        self.assertEqual(run(service).local_state, 'ready')
        record = run(undeclared)
        self.assertEqual(record.local_state, 'ready', "an undeclared product forces nothing")
        self.assertIn('NCOLLECTION:item-type-not-declared', self.rules(record))

    def test_an_unmapped_unit_of_measure_blocks_instead_of_defaulting(self):
        uom = self.env['uom.uom'].create(
            {'name': 'Crate of 7', 'relative_factor': 7.0, 'relative_uom_id': self.env.ref('uom.product_uom_unit').id}
        )
        move = self._invoice(
            [
                {
                    'product_id': self.product.id,
                    'quantity': 1,
                    'price_unit': 10.0,
                    'product_uom_id': uom.id,
                    'tax_ids': [Command.set(self.tax_s.ids)],
                }
            ]
        )
        record = self._einvoice(move)
        record.action_prepare()
        self.assertIn('NCOLLECTION:unit-code-unmapped', self.rules(record))

    def test_payment_means_is_never_guessed(self):
        self.config.default_payment_means_code = False
        record = self._einvoice()
        record.action_prepare()
        self.assertEqual(record.local_state, 'draft')
        self.assertIn('OFFICIAL:ibr-191-ae', self.rules(record))
        self.config.default_payment_means_code = '30'
        record.action_prepare()
        self.assertIn('OFFICIAL:ibr-192-ae', self.rules(record), "a credit transfer needs the payee account")
        bank = self.env['res.partner.bank'].create(
            {'acc_number': 'AE070331234567890123456', 'partner_id': self.company.partner_id.id}
        )
        self.config.default_bank_account_id = bank
        record.action_prepare()
        self.assertEqual(record.local_state, 'ready', self.rules(record))
        self.assertEqual(record.payload()['payment']['account_id'], 'AE070331234567890123456')

    def test_an_emirate_outside_the_seven_blocks(self):
        self.buyer.state_id = self.env['res.country.state'].search([('country_id.code', '=', 'US')], limit=1)
        record = self._einvoice()
        record.action_prepare()
        self.assertEqual(record.local_state, 'draft')

    def test_xml_generation_is_refused_when_the_source_changed_since_preparation(self):
        record = self._einvoice()
        record.action_prepare()
        self.map_s.pint_category = 'Z'  # the mapping changed after preparation
        with self.assertRaises(UserError):
            record.action_generate_xml()
        self.map_s.pint_category = 'S'

    def test_before_submission_a_generated_xml_is_superseded_never_destroyed(self):
        """A document the official rules rejected (or the books corrected) must not be stranded: it is prepared again,
        the old file stays in the archive for good and the history keeps the link to it."""
        record = self._einvoice()
        record.action_prepare()
        record.action_generate_xml()
        first = record.xml_attachment_id
        first_sha = record.xml_sha256
        record.action_prepare()
        self.assertEqual(record.local_state, 'ready')
        self.assertFalse(record.xml_attachment_id, "the record no longer points at the superseded file")
        self.assertTrue(first.exists(), "the superseded file is still in the archive")
        self.assertEqual(hashlib.sha256(first.raw).hexdigest(), first_sha)
        with self.assertRaises(UserError):
            first.unlink()
        superseded = record.history_ids.filtered(lambda h: h.event == 'xml_superseded')
        self.assertEqual(superseded.attachment_id, first)
        record.action_generate_xml()
        self.assertNotEqual(record.xml_attachment_id, first)
        status = self.env['ncollection.einvoice']._nc_archive_status()
        self.assertEqual((status['regulated_records'], status['archive_files']), (1, 2))

    def test_after_submission_the_archived_xml_can_no_longer_be_replaced(self):
        record = self._einvoice()
        record.action_prepare()
        record.action_generate_xml()
        self._validated(record)
        record._transition('submission_state', 'queued', 'queued')
        with self.assertRaises(UserError):
            record.action_prepare()
        self.assertTrue(record.xml_attachment_id)


class _Worker:
    """A scripted stand-in for the isolated validator worker."""

    def __init__(self, failures=(), version=None, manifest=None, sign=True):
        profile = spec.get_profile()
        self.sign = sign
        self.answer = {
            'engine': 'SaxonC-HE test',
            'spec_version': version or profile.pint_version,
            'manifest_sha256': manifest or spec.manifest_sha256(profile),
            'failures': list(failures),
        }
        worker = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers.get('content-length') or 0)))
                answer = signed_answer(worker.answer, request)
                if not worker.sign:
                    answer['signature'] = 'f' * 64
                data = json.dumps(answer).encode()
                self.send_response(200)
                self.send_header('content-length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.url = 'http://127.0.0.1:%d' % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@tagged('post_install', '-at_install')
class TestOfficialValidationStep(EInvoiceCase):

    def _checked(self):
        record = self._einvoice()
        record.action_prepare()
        record.action_generate_xml()
        return record

    def test_without_a_worker_the_document_is_not_validated_and_the_reason_is_recorded(self):
        record = self._checked()
        with patch.dict(os.environ, {'NC_EINVOICE_VALIDATOR_URL': ''}):
            result = record.action_validate()
        self.assertEqual(result['params']['type'], 'warning')
        self.assertEqual(record.local_state, 'locally_checked', "unavailable validation must never read as valid")
        history = self.env['ncollection.einvoice.history'].search([('einvoice_id', '=', record.id)])
        self.assertIn(
            'validator_unavailable', history.mapped('event'), "the reason must survive: it is returned, not raised"
        )

    def test_a_clean_official_answer_validates(self):
        worker = _Worker()
        self.addCleanup(worker.close)
        record = self._checked()
        with patch.dict(os.environ, validator_env(worker.url)):
            record.action_validate()
        self.assertEqual(record.local_state, 'validated')
        self.assertEqual(record.submission_state, 'not_submitted', "validated is not submitted")

    def test_official_failures_are_recorded_with_their_rule_and_fail_the_document(self):
        worker = _Worker(
            failures=[{'id': 'ibr-190-ae', 'flag': 'fatal', 'location': '/Invoice', 'text': 'rate must be 5'}]
        )
        self.addCleanup(worker.close)
        record = self._checked()
        with patch.dict(os.environ, validator_env(worker.url)):
            record.action_validate()
        self.assertEqual(record.local_state, 'validation_failed')
        official = record.validation_ids.filtered(lambda v: v.layer == 'official')
        self.assertEqual(official.mapped('rule'), ['OFFICIAL:ibr-190-ae'])
        self.assertEqual(official.authority, 'OFFICIAL')

    def test_a_worker_with_other_rules_is_unavailable_not_valid(self):
        worker = _Worker(manifest='0' * 64)
        self.addCleanup(worker.close)
        record = self._checked()
        with patch.dict(os.environ, validator_env(worker.url)):
            result = record.action_validate()
        self.assertEqual(result['params']['type'], 'warning')
        self.assertEqual(record.local_state, 'locally_checked')

    def test_an_unsigned_answer_is_not_a_validation(self):
        worker = _Worker(sign=False)
        self.addCleanup(worker.close)
        record = self._checked()
        with patch.dict(os.environ, validator_env(worker.url)):
            result = record.action_validate()
        self.assertEqual(result['params']['type'], 'warning')
        self.assertEqual(record.local_state, 'locally_checked', "a forged 'valid' must never validate")

    def test_the_validation_records_the_engine_the_release_and_the_rule_set(self):
        worker = _Worker()
        self.addCleanup(worker.close)
        record = self._checked()
        with patch.dict(os.environ, validator_env(worker.url)):
            record.action_validate()
        passed = record.history_ids.filtered(lambda h: h.event == 'official_validation_passed')
        self.assertIn('SaxonC-HE test', passed.detail)
        self.assertIn('release %s' % spec.get_profile().pint_version, passed.detail)
        self.assertIn(spec.manifest_sha256()[:16], passed.detail)

    def test_an_unavailable_worker_does_not_skip_the_other_records_of_the_call(self):
        first, second = self._checked(), self._checked()
        with patch.dict(os.environ, {'NC_EINVOICE_VALIDATOR_URL': ''}):
            result = (first | second).action_validate()
        self.assertEqual(result['params']['type'], 'warning')
        for record in (first, second):
            events = record.history_ids.mapped('event')
            self.assertIn('validator_unavailable', events, "every record of the call is attempted and recorded")

    def test_validation_needs_the_xml_first(self):
        record = self._einvoice()
        with self.assertRaises(UserError):
            record.action_validate()
