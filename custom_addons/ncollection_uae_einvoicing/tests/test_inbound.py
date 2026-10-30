# -*- coding: utf-8 -*-
"""Inbound e-invoices (models/inbound.py, lib/inbound_parse.py): received document -> at most a DRAFT vendor bill.

The fixture is REAL: the seller company's own engine turns a posted customer invoice into PINT-AE XML, addressed to a
customer whose identifiers are those of a second company (the receiving tenant). That company's ASP (the contract
adapter) then delivers the XML, and the inbound engine processes it as the receiving company."""

import re
from datetime import date, timedelta
from unittest.mock import patch

import psycopg2

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged
from odoo.tools import mute_logger

from odoo.addons.queue_job.tests.common import trap_jobs

from ..lib import adapter_contract as contract
from ..lib import inbound_parse
from . import fake_adapter
from .common import EInvoiceCase

BUYER_TIN, BUYER_TRN = '1345678901', '134567890123003'
SELLER_TIN, SELLER_TRN = '1987654321', '198765432102003'


class InboundCase(EInvoiceCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # the seller company's own partner is scoped to it, so only the receiving company's suppliers can match
        cls.company.partner_id.sudo().write({'company_id': cls.company.id})
        cls.tenant = cls._company('Receiving Tenant LLC', tin=BUYER_TIN, trn=BUYER_TRN, registry='112345679000001')
        cls.env['res.currency'].sudo().search([('name', '=', 'AED')]).write({'active': True})
        cls.provider = cls.env['ncollection.einvoice.provider'].sudo().create(
            {'name': 'Fake ASP (tenant)', 'company_id': cls.tenant.id})
        cls.env.cr.execute('UPDATE ncollection_einvoice_provider SET adapter = %s WHERE id = %s',
                           (fake_adapter.FakeAdapter.code, cls.provider.id))
        cls.provider.invalidate_recordset()
        cls.supplier = cls.env['res.partner'].create({
            'name': 'EInv Seller Co', 'company_type': 'company', 'company_id': cls.tenant.id,
            'nc_trn': SELLER_TRN, 'country_id': cls.ae.id})
        cls.purchase_tax = cls.env['account.tax'].create({
            'name': 'Purchase 5% (inbound test)', 'amount': 5.0, 'type_tax_use': 'purchase',
            'company_id': cls.tenant.id})
        cls.env['ncollection.einvoice.tax.map'].create(
            {'company_id': cls.tenant.id, 'tax_id': cls.purchase_tax.id, 'pint_category': 'S'})

    def setUp(self):
        super().setUp()
        self.adapter = fake_adapter.FakeAdapter(self.provider, lambda name: 'secret')
        for target in ('_nc_adapter',):
            patcher = patch.object(type(self.env['ncollection.einvoice']), target,
                                   lambda rec, provider: self.adapter)
            patcher.start()
            self.addCleanup(patcher.stop)
        official = patch.object(type(self.env['ncollection.einvoice.inbound']), '_nc_official_check',
                                lambda rec, xml, kind, profile=None: [])
        official.start()
        self.official = official
        self.addCleanup(official.stop)

    def _xml(self, move=None, **vals):
        record = self._einvoice(move or self._invoice(), **vals)
        record.action_prepare()
        record.action_generate_xml()
        return record.xml_attachment_id.raw

    def _receive(self, xml, document_id='IN-1', message_id=None):
        self.adapter.receive(document_id, xml, message_id=message_id)
        Inbound = self.env['ncollection.einvoice.inbound']
        with trap_jobs():
            record = Inbound._nc_receive(self.provider, document_id)
            record._nc_process()
        return record

    def _bills(self):
        return self.env['account.move'].sudo().search(
            [('nc_inbound_id', '!=', False), ('company_id', '=', self.tenant.id)])

    def _flush_every_write(self):
        """What OCA auditlog's ``write_full`` does on a model it audits: every write reaches the database at once
        (``flush_recordset``), instead of when the ORM next flushes. The full CI matrix found duplicate handling
        crashing under exactly that; emulated here so the guard holds whether or not auditlog is installed."""
        Inbound = type(self.env['ncollection.einvoice.inbound'])
        write = Inbound.write

        def flushed_write(record, vals):
            result = write(record, vals)
            record.flush_recordset()
            return result
        return patch.object(Inbound, 'write', flushed_write)


@tagged('post_install', '-at_install')
class TestInboundHappyPath(InboundCase):

    def test_a_valid_invoice_becomes_a_draft_vendor_bill(self):
        record = self._receive(self._xml())
        self.assertEqual(record.state, 'bill_created', record.reason)
        bill = record.bill_id
        self.assertEqual(bill.state, 'draft', 'NEVER posted automatically')
        self.assertEqual(bill.move_type, 'in_invoice')
        self.assertEqual(bill.company_id, self.tenant)
        self.assertEqual(bill.partner_id, self.supplier)
        self.assertEqual(bill.ref, record.invoice_number)
        self.assertEqual(bill.currency_id.name, 'AED')
        self.assertEqual(bill.invoice_date, record.issue_date)
        self.assertAlmostEqual(bill.amount_untaxed, 200.0)
        self.assertAlmostEqual(bill.amount_tax, 10.0)
        self.assertEqual(bill.invoice_line_ids.tax_ids, self.purchase_tax)
        self.assertEqual(bill.nc_inbound_id, record, 'the bill is traceable to the received e-invoice')
        self.assertIn(record.asp_document_id, bill.narration)
        self.assertEqual(record.match_basis, 'trn')
        self.assertEqual(record.history_ids.mapped('to_state'),
                         ['received', 'validating', 'matching', 'ready', 'bill_created'])

    def test_the_xml_is_archived_with_its_digest(self):
        xml = self._xml()
        record = self._receive(xml)
        self.assertEqual(record.xml_attachment_id.raw, xml)
        self.assertEqual(record.xml_sha256, __import__('hashlib').sha256(xml).hexdigest())

    def test_a_credit_note_becomes_a_draft_refund_referencing_its_invoice(self):
        original = self._invoice(invoice_date=date(2026, 10, 1), invoice_date_due=date(2026, 10, 31))
        refund = self._invoice(move_type='out_refund', reversed_entry_id=original.id,
                               invoice_date=date(2026, 10, 5), invoice_date_due=date(2026, 10, 5))
        record = self._receive(self._xml(refund, credit_reason='DL8.61.1.E'))
        self.assertEqual(record.state, 'bill_created', record.reason)
        self.assertEqual(record.bill_id.move_type, 'in_refund')
        self.assertEqual(record.bill_id.state, 'draft')
        self.assertTrue(record.billing_reference)

    def test_a_changed_supplier_name_with_the_same_trn_still_matches(self):
        xml = self._xml().replace(b'EInv Seller Co', b'EInv Seller Co (renamed)')
        record = self._receive(xml)
        self.assertEqual(record.partner_id, self.supplier)
        self.assertEqual(record.state, 'bill_created', record.reason)


@tagged('post_install', '-at_install')
class TestInboundDuplicates(InboundCase):

    def test_the_same_provider_document_twice_is_one_record(self):
        xml = self._xml()
        first = self._receive(xml)
        again = self._receive(xml)
        self.assertEqual(first, again)
        self.assertEqual(len(self._bills()), 1)

    def test_the_same_document_under_a_new_provider_id_is_a_duplicate(self):
        xml = self._xml()
        first = self._receive(xml, 'IN-1')
        second = self._receive(xml, 'IN-2', message_id='other-message')
        self.assertEqual(second.state, 'duplicate')
        self.assertEqual(second.duplicate_of_id, first)
        self.assertFalse(second.bill_id)
        self.assertEqual(len(self._bills()), 1, 'no duplicate accounting document')

    def test_a_duplicate_is_classified_when_every_write_reaches_the_database_at_once(self):
        xml = self._xml()
        first = self._receive(xml, 'IN-1')
        with self._flush_every_write():
            second = self._receive(xml, 'IN-2', message_id='other-message')
        self.assertEqual((second.state, second.duplicate_of_id, second.domain_key),
                         ('duplicate', first, first.domain_key))
        self.assertEqual(second.history_ids.sorted('id')[-1:].to_state, 'duplicate')
        self.assertFalse(second.bill_id)
        self.assertEqual(len(self._bills()), 1, 'no duplicate accounting document')

    def test_a_concurrent_duplicate_is_classified_when_every_write_reaches_the_database_at_once(self):
        """The race path: the lookup sees no original (another worker has not committed yet), the database index
        refuses the identity, and the row must still end as a duplicate of the original."""
        xml = self._xml()
        first = self._receive(xml, 'IN-1')
        Inbound = type(self.env['ncollection.einvoice.inbound'])
        search = Inbound.search
        missed = []

        def search_missing_the_original_once(model, domain, *args, **kwargs):
            if not missed and ('state', '!=', 'duplicate') in domain and any(
                    isinstance(term, tuple) and term[0] == 'id' for term in domain):
                missed.append(True)
                return model.browse()
            return search(model, domain, *args, **kwargs)
        with self._flush_every_write(), patch.object(Inbound, 'search', search_missing_the_original_once), \
                mute_logger('odoo.sql_db'):
            second = self._receive(xml, 'IN-2', message_id='other-message')
        self.assertTrue(missed, 'the race path was not exercised')
        self.assertEqual((second.state, second.duplicate_of_id), ('duplicate', first))
        self.assertEqual(second.reason, 'concurrent delivery')
        self.assertEqual(len(self._bills()), 1, 'no duplicate accounting document')

    def test_a_callback_racing_a_poll_fetches_once(self):
        xml = self._xml()
        self.adapter.receive('IN-R', xml)
        event = contract.CallbackEvent(kind='inbound', status='received', correlation_id=None,
                                       asp_document_id='IN-R', occurred_at=None, message_id='cb-1')
        Inbound = self.env['ncollection.einvoice.inbound']
        with trap_jobs() as trap:
            self.env['ncollection.einvoice.event']._nc_ingest(self.provider, [event], 'callback')
            Inbound._nc_job_poll(self.provider.id)
            self.env['ncollection.einvoice.event']._nc_ingest(self.provider, [event], 'callback')
        trap.assert_jobs_count(1, only=Inbound._nc_job_fetch)  # identity key collapses the second enqueue
        with trap_jobs():
            Inbound._nc_job_fetch(self.provider.id, 'IN-R')
            Inbound._nc_job_fetch(self.provider.id, 'IN-R')  # the worker retried / restarted
        self.assertEqual(Inbound.sudo().search_count([('asp_document_id', '=', 'IN-R')]), 1)
        self.assertEqual(len(self._bills()), 1)

    def test_processing_again_after_a_restart_creates_nothing_new(self):
        record = self._receive(self._xml())
        with trap_jobs():
            self.assertEqual(record._nc_process(), 'bill_created')
            self.assertEqual(record._nc_job_process(), 'bill_created')
        self.assertEqual(len(self._bills()), 1)

    def test_the_database_refuses_a_second_bill_for_one_document(self):
        record = self._receive(self._xml())
        with self.assertRaises(psycopg2.errors.UniqueViolation), mute_logger('odoo.sql_db'), \
                self.env.cr.savepoint():
            record.bill_id.sudo().copy({'nc_inbound_id': record.id})
            self.env.flush_all()

    def test_the_database_refuses_a_second_record_of_one_domain_identity(self):
        first = self._receive(self._xml())
        with self.assertRaises(psycopg2.errors.UniqueViolation), mute_logger('odoo.sql_db'), \
                self.env.cr.savepoint():
            self.env['ncollection.einvoice.inbound'].sudo().create({
                'provider_id': self.provider.id, 'company_id': self.tenant.id, 'message_id': 'x',
                'asp_document_id': 'x', 'domain_key': first.domain_key, 'state': 'ready'})
            self.env.flush_all()

    def test_a_locked_record_is_left_to_its_worker(self):
        xml = self._xml()
        self.adapter.receive('IN-L', xml)
        record = self.env['ncollection.einvoice.inbound']._nc_receive(self.provider, 'IN-L')
        with patch.object(type(record), '_nc_lock', lambda self: False):
            self.assertEqual(record._nc_process(), 'locked by another worker')
        self.assertEqual(record.state, 'received')


@tagged('post_install', '-at_install')
class TestInboundValidation(InboundCase):

    def test_malformed_xml_fails_without_a_bill(self):
        record = self._receive(b'<Invoice><broken>')
        self.assertEqual(record.state, 'validation_failed')
        self.assertEqual(record.reason_code, 'NCOLLECTION:inbound_malformed')
        self.assertFalse(self._bills())

    def test_a_doctype_or_entity_is_refused(self):
        xml = (b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]>'
               b'<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2">&e;</Invoice>')
        record = self._receive(xml)
        self.assertEqual(record.reason_code, 'NCOLLECTION:inbound_doctype')
        with self.assertRaises(inbound_parse.InboundError):
            inbound_parse.parse(xml)

    def test_an_official_rule_failure_stops_it(self):
        self.official.stop()
        failing = patch.object(type(self.env['ncollection.einvoice.inbound']), '_nc_official_check',
                               lambda rec, xml, kind, profile=None: ['ibr-193-ae'])
        failing.start()
        self.addCleanup(failing.stop)
        record = self._receive(self._xml())
        self.assertEqual(record.state, 'validation_failed')
        self.assertEqual(record.reason_code, 'OFFICIAL:ibr-193-ae')
        self.assertFalse(self._bills())

    def test_an_unavailable_validator_waits_then_continues(self):
        self.official.stop()
        answers = [None, []]
        waiting = patch.object(type(self.env['ncollection.einvoice.inbound']), '_nc_official_check',
                               lambda rec, xml, kind, profile=None: answers.pop(0))
        waiting.start()
        self.addCleanup(waiting.stop)
        record = self._receive(self._xml())
        self.assertEqual(record.state, 'validating')
        self.assertGreater(record.next_attempt_at, fields.Datetime.now())
        self.assertEqual(record._nc_job_process(), 'not yet')
        record.write({'next_attempt_at': fields.Datetime.now() - timedelta(seconds=1)})
        with trap_jobs():
            record._nc_job_process()
        self.assertEqual(record.state, 'bill_created')

    def test_a_document_for_another_company_is_rejected(self):
        stranger = self._partner('Someone Else LLC', tin='1999999999', trn='199999999900003')
        record = self._receive(self._xml(self._invoice(partner=stranger)))
        self.assertEqual(record.state, 'rejected')
        self.assertEqual(record.reason_code, 'NCOLLECTION:inbound_wrong_recipient')
        self.assertFalse(self._bills())

    def test_wrong_totals_fail(self):
        xml = re.sub(rb'(<cbc:TaxInclusiveAmount currencyID="AED">)[^<]+', rb'\g<1>999.00', self._xml())
        record = self._receive(xml)
        self.assertEqual(record.state, 'validation_failed')
        self.assertIn('inclusive', record.reason)

    def test_a_self_billed_document_naming_us_as_buyer_is_rejected(self):
        """A 389 is issued BY the buyer: one that arrives naming this company as its buyer is not for us to book
        (this company would have issued it). Cycle 3 replaced the earlier 'review' stop; tests/test_self_billing.py
        covers the received self-billed document that becomes a draft customer invoice."""
        xml = self._xml().replace(b'<cbc:InvoiceTypeCode>380<', b'<cbc:InvoiceTypeCode>389<')
        record = self._receive(xml)
        self.assertEqual(record.state, 'rejected')
        self.assertEqual(record.reason_code, 'NCOLLECTION:inbound_wrong_recipient')
        self.assertFalse(self._bills())

    def test_a_document_whose_digest_disagrees_with_the_provider_is_refused(self):
        xml = self._xml()
        self.adapter.receive('IN-D', xml)
        self.adapter.inbox['IN-D'] = contract.InboundDocument('IN-D', 'm', xml, xml_sha256='0' * 64)
        with self.assertRaises(contract.AdapterError):
            self.env['ncollection.einvoice.inbound']._nc_receive(self.provider, 'IN-D')


@tagged('post_install', '-at_install')
class TestInboundMatching(InboundCase):

    def test_no_supplier_is_unmatched_and_never_created(self):
        self.supplier.write({'nc_trn': False})
        partners = self.env['res.partner'].sudo().search_count([])
        record = self._receive(self._xml())
        self.assertEqual(record.state, 'unmatched')
        self.assertEqual(record.reason_code, 'NCOLLECTION:inbound_no_supplier')
        self.assertEqual(self.env['res.partner'].sudo().search_count([]), partners, 'no partner was invented')
        self.supplier.write({'nc_trn': SELLER_TRN})
        accountant = self._user('inbound_reprocessor', ['ncollection_uae_einvoicing.group_einvoice_user'],
                                company=self.tenant)
        with trap_jobs():
            record.with_user(accountant).with_context(allowed_company_ids=self.tenant.ids).action_reprocess()
            record._nc_job_process()
        self.assertEqual(record.state, 'bill_created')

    def test_two_suppliers_with_the_same_trn_are_ambiguous(self):
        self.env['res.partner'].create({'name': 'Twin', 'company_type': 'company', 'company_id': self.tenant.id,
                                        'nc_trn': SELLER_TRN})
        record = self._receive(self._xml())
        self.assertEqual(record.state, 'unmatched')
        self.assertEqual(record.reason_code, 'NCOLLECTION:inbound_ambiguous_supplier')
        self.assertFalse(self._bills())

    def test_identifiers_pointing_to_different_suppliers_conflict(self):
        self.env['res.partner'].create({'name': 'Other by TIN', 'company_type': 'company',
                                        'company_id': self.tenant.id, 'nc_tin': SELLER_TIN})
        record = self._receive(self._xml())
        self.assertEqual(record.reason_code, 'NCOLLECTION:inbound_ambiguous_supplier')

    def test_a_name_alone_never_matches(self):
        self.supplier.write({'nc_trn': False})
        self.env['res.partner'].create({'name': 'EInv Seller Co', 'company_type': 'company',
                                        'company_id': self.tenant.id})
        record = self._receive(self._xml())
        self.assertEqual(record.state, 'unmatched')

    def test_an_unmapped_tax_needs_review(self):
        self.env['ncollection.einvoice.tax.map'].search([('tax_id', '=', self.purchase_tax.id)]).unlink()
        record = self._receive(self._xml())
        self.assertEqual(record.state, 'review')
        self.assertEqual(record.reason_code, 'NCOLLECTION:inbound_tax_unmapped')
        self.assertFalse(self._bills())

    def test_an_inactive_currency_needs_review(self):
        xml = self._xml().replace(b'<cbc:DocumentCurrencyCode>AED<', b'<cbc:DocumentCurrencyCode>XAU<')
        xml = xml.replace(b'currencyID="AED"', b'currencyID="XAU"')
        record = self._receive(xml)
        self.assertEqual(record.state, 'review')
        self.assertEqual(record.reason_code, 'NCOLLECTION:inbound_currency')


@tagged('post_install', '-at_install')
class TestInboundSecurity(InboundCase):

    def test_users_cannot_write_or_create_intake_records(self):
        record = self._receive(self._xml())
        user = self._user('inbound_manager', ['ncollection_uae_einvoicing.group_einvoice_manager'],
                          company=self.tenant)
        with self.assertRaises(UserError):
            record.with_user(user).write({'state': 'ready'})
        with self.assertRaises(UserError):
            self.env['ncollection.einvoice.inbound'].with_user(user).create({
                'provider_id': self.provider.id, 'company_id': self.tenant.id, 'message_id': 'm',
                'asp_document_id': 'a'})
        with self.assertRaises(UserError):
            record.with_user(user).unlink()

    def test_another_company_cannot_see_the_document(self):
        record = self._receive(self._xml())
        outsider = self._user('inbound_outsider', ['ncollection_uae_einvoicing.group_einvoice_manager'],
                              company=self.company)
        self.assertFalse(self.env['ncollection.einvoice.inbound'].with_user(outsider).search(
            [('id', '=', record.id)]))

    def test_a_forged_inbound_callback_announces_nothing(self):
        body = b'{"events": [{"kind": "inbound", "status": "received", "message_id": "f", "asp_document_id": "X"}]}'
        headers = {'X-Fake-Timestamp': str(int(self.adapter.clock())), 'X-Fake-Nonce': 'n',
                   'X-Fake-Signature': fake_adapter.sign('wrong-secret', int(self.adapter.clock()), 'n', body)}
        self.assertFalse(self.adapter.verify_callback(headers, body))

    def test_the_intake_is_always_the_providers_company(self):
        record = self._receive(self._xml())
        self.assertEqual(record.company_id, self.provider.company_id)

    def test_a_plain_user_cannot_reprocess_another_companys_document(self):
        self.supplier.write({'nc_trn': False})
        record = self._receive(self._xml())
        outsider = self._user('inbound_outsider2', ['ncollection_uae_einvoicing.group_einvoice_user'],
                              company=self.company)
        with self.assertRaises(AccessError):
            record.with_user(outsider).action_reprocess()


@tagged('post_install', '-at_install')
class TestInboundParser(InboundCase):

    def test_the_parser_reads_what_the_engine_wrote(self):
        facts = inbound_parse.parse(self._xml())
        self.assertEqual(facts['kind'], 'invoice')
        self.assertEqual(facts['type_code'], '380')
        self.assertEqual(facts['seller']['trn'], SELLER_TRN)
        self.assertEqual(facts['buyer']['trn'], BUYER_TRN)
        self.assertEqual(len(facts['lines']), 1)
        self.assertEqual(inbound_parse.check(facts), [])

    def test_a_credit_note_without_reference_is_refused(self):
        facts = inbound_parse.parse(self._xml())
        facts.update(kind='credit_note', billing_reference=None)
        self.assertIn('missing_reference', [code for code, _m in inbound_parse.check(facts)])

    def test_non_ubl_roots_are_refused(self):
        with self.assertRaises(inbound_parse.InboundError) as caught:
            inbound_parse.parse(b'<Order xmlns="urn:x"/>')
        self.assertEqual(caught.exception.code, 'not_ubl')
