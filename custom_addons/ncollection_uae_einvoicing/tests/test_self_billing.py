# -*- coding: utf-8 -*-
"""PINT AE Self-Billing (389 / 261) end to end on REAL posted vendor bills, and the received side.

Guidelines V2.0 §10.3: the BUYER (this company) issues the document on behalf of its supplier, under an agreement,
only for a VAT-registered supplier, only while the buyer is on the Electronic Invoicing System, and only as a TAX
document. The supplier stays the SELLER of the document (AccountingSupplierParty); the XML carries the Self-Billing
CustomizationID / ProfileID and is validated by the worker that holds the Self-Billing rule set."""

import json
import os
from datetime import date, timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from odoo.addons.queue_job.tests.common import trap_jobs

from ..lib import spec, validator
from . import fake_adapter
from .common import EInvoiceCase
from .test_real_move_crosscheck import EXPORT_ENV, REQUIRED_CASES

SUPPLIER_TIN, SUPPLIER_TRN = '1456789012', '145678901234003'


class SelfBillingCase(EInvoiceCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.supplier = cls._partner(
            'Gulf Supplies LLC', tin=SUPPLIER_TIN, trn=SUPPLIER_TRN, state='SH', registry='112345670000002',
            city='Sharjah'
        )
        cls.supplier_bank = cls.env['res.partner.bank'].create(
            {'partner_id': cls.supplier.id, 'acc_number': 'AE070331234567890123456'}
        )
        Tax, Map = cls.env['account.tax'], cls.env['ncollection.einvoice.tax.map']
        cls.ptax_s = Tax.create(
            {'name': 'Purchase 5% (SB)', 'amount': 5.0, 'type_tax_use': 'purchase', 'company_id': cls.company.id}
        )
        cls.ptax_e = Tax.create(
            {'name': 'Purchase exempt (SB)', 'amount': 0.0, 'type_tax_use': 'purchase', 'company_id': cls.company.id}
        )
        cls.ptax_z = Tax.create(
            {'name': 'Purchase 0% (SB)', 'amount': 0.0, 'type_tax_use': 'purchase', 'company_id': cls.company.id}
        )
        Map.create({'company_id': cls.company.id, 'tax_id': cls.ptax_s.id, 'pint_category': 'S'})
        Map.create(
            {'company_id': cls.company.id, 'tax_id': cls.ptax_e.id, 'pint_category': 'E', 'exemption_code': 'DL8.46.1'}
        )
        Map.create({'company_id': cls.company.id, 'tax_id': cls.ptax_z.id, 'pint_category': 'Z'})
        cls.manager = cls._user(
            'sb-manager', ['base.group_user', 'account.group_account_manager', 'ncollection_uae_einvoicing.'
                           'group_einvoice_manager']
        )
        cls.agreement = cls._agreement(cls.supplier)

    @classmethod
    def _agreement(cls, partner, direction='issue', date_from=date(2026, 1, 1), date_to=None, activate=True,
                   company=None):
        agreement = cls.env['ncollection.einvoice.self.billing.agreement'].create(
            {
                'name': 'SBA-%s-%s' % (partner.id, direction),
                'company_id': (company or cls.company).id,
                'partner_id': partner.id,
                'direction': direction,
                'date_from': date_from,
                'date_to': date_to,
            }
        )
        if activate:
            agreement.action_activate()
        return agreement

    def _bill(self, lines=None, move_type='in_invoice', partner=None, bank=True, post=True, **extra):
        lines = lines or [
            {'product_id': self.product.id, 'quantity': 2, 'price_unit': 100.0,
             'tax_ids': [Command.set(self.ptax_s.ids)]}
        ]
        values = {
            'move_type': move_type,
            'partner_id': (partner or self.supplier).id,
            'company_id': self.company.id,
            'invoice_date': fields.Date.today(),
            'invoice_date_due': fields.Date.add(fields.Date.today(), days=30),
            'invoice_line_ids': [Command.create(line) for line in lines],
        }
        if bank:
            values['partner_bank_id'] = self.supplier_bank.id
        values.update(extra)
        move = self.env['account.move'].create(values)
        if post:
            move.action_post()
        return move

    def _ready(self, move, **vals):
        record = self._einvoice(move, **vals)
        record.action_prepare()
        self.assertEqual(record.local_state, 'ready', sorted(self.rules(record)))
        return record

    def _xml(self, move, **vals):
        record = self._ready(move, **vals)
        record.action_generate_xml()
        self.assertEqual(record.local_state, 'locally_checked')
        return record, record.xml_attachment_id.raw

    def _blocked(self, move, rule, **vals):
        record = self._einvoice(move, **vals)
        record.action_prepare()
        self.assertEqual(record.local_state, 'draft')
        self.assertIn(rule, self.rules(record))
        return record

    def _export(self, case, xml):
        folder = os.environ.get(EXPORT_ENV)
        if folder:
            kind, expect, rule = REQUIRED_CASES[case][:3]
            with open(os.path.join(folder, case + '.xml'), 'wb') as handle:
                handle.write(xml)
            with open(os.path.join(folder, case + '.json'), 'w', encoding='utf-8') as handle:
                json.dump({'case': case, 'kind': kind, 'expect': expect, 'rule': rule}, handle)


@tagged('post_install', '-at_install')
class TestSelfBillingEligibility(SelfBillingCase):

    def test_a_vendor_bill_without_an_agreement_cannot_become_an_einvoice(self):
        other = self._partner('No Agreement Trading LLC', tin='1567890123', trn='156789012345003', state='DU')
        with self.assertRaisesRegex(UserError, 'no active self-billing agreement'):
            self._einvoice(self._bill(partner=other))

    def test_a_draft_agreement_is_not_an_agreement(self):
        other = self._partner('Draft Agreement LLC', tin='1567890124', trn='156789012346003', state='DU')
        self._agreement(other, activate=False)
        with self.assertRaisesRegex(UserError, 'no active self-billing agreement'):
            self._einvoice(self._bill(partner=other))

    def test_an_agreement_cannot_be_activated_for_a_supplier_without_trn(self):
        unregistered = self._partner('Unregistered Supplies', tin='1567890125', state='DU')
        agreement = self._agreement(unregistered, activate=False)
        with self.assertRaisesRegex(UserError, 'not registered for VAT'):
            agreement.action_activate()
        self.assertEqual(agreement.state, 'draft')

    def test_only_a_manager_manages_agreements(self):
        clerk = self._user('sb-clerk', ['base.group_user', 'ncollection_uae_einvoicing.group_einvoice_user'])
        Agreement = self.env['ncollection.einvoice.self.billing.agreement'].with_user(clerk)
        with self.assertRaises(AccessError):
            Agreement.create({'name': 'X', 'partner_id': self.supplier.id, 'date_from': date(2026, 1, 1)})
        with self.assertRaises(AccessError):
            self.agreement.with_user(clerk).action_terminate()
        # the manager can, and the person and time are recorded
        mine = self._agreement(self.buyer, direction='receive', activate=False)
        mine.with_user(self.manager).action_activate()
        self.assertEqual((mine.state, mine.activated_by_id), ('active', self.manager))

    def test_an_active_agreement_is_immutable_and_never_deleted(self):
        with self.assertRaisesRegex(UserError, 'cannot be changed'):
            self.agreement.with_user(self.manager).write({'date_to': date(2030, 1, 1)})
        with self.assertRaisesRegex(UserError, 'state changes only'):
            self.agreement.with_user(self.manager).write({'state': 'draft'})
        with self.assertRaisesRegex(UserError, 'never deleted'):
            self.agreement.unlink()

    def test_overlapping_agreements_cannot_both_be_active(self):
        second = self._agreement(self.supplier, activate=False, date_from=date(2026, 6, 1))
        with self.assertRaisesRegex(UserError, 'covers part of the same period'):
            second.action_activate()

    def test_the_agreement_must_cover_the_transaction_date(self):
        late = self._partner('Late Agreement LLC', tin='1567890126', trn='156789012347003', state='DU')
        future = self._agreement(late, date_from=fields.Date.add(fields.Date.today(), days=10))
        self._blocked(self._bill(partner=late), 'NCOLLECTION:self-billing-agreement-not_yet')
        ended = self._partner('Ended Agreement LLC', tin='1567890127', trn='156789012348003', state='DU')
        self._agreement(ended, date_from=date(2025, 1, 1), date_to=date(2025, 12, 31))
        self._blocked(self._bill(partner=ended), 'NCOLLECTION:self-billing-agreement-expired')
        self.assertEqual(future.state, 'active')

    def test_a_terminated_agreement_blocks_the_next_preparation(self):
        record = self._ready(self._bill())
        self.agreement.action_terminate()
        record.action_prepare()
        self.assertEqual(record.local_state, 'draft')
        self.assertIn('NCOLLECTION:self-billing-agreement-missing', self.rules(record))

    def test_a_supplier_that_lost_its_trn_is_blocked(self):
        record = self._ready(self._bill())
        self.supplier.write({'nc_trn': False})
        record.action_prepare()
        self.assertIn('NCOLLECTION:self-billing-supplier-not-registered', self.rules(record))
        self.assertEqual(record.local_state, 'draft')

    def test_the_buyer_must_be_on_the_einvoicing_system(self):
        self._blocked(self._bill(), 'NCOLLECTION:self-billing-buyer-not-on-system', buyer_not_onboarded=True)

    def test_there_is_no_self_billed_commercial_invoice(self):
        record = self._blocked(
            self._bill(lines=[{'product_id': self.product.id, 'quantity': 1, 'price_unit': 100.0,
                               'tax_ids': [Command.set(self.ptax_e.ids)]}]),
            'NCOLLECTION:self-billing-commercial-not-allowed',
        )
        self.assertEqual(record.type_code, '389', 'never 480 under self-billing (Guidelines V2.0 §10.3)')

    def test_a_self_billed_export_is_refused(self):
        self._blocked(self._bill(), 'NCOLLECTION:self-billing-export-not-applicable', flag_export=True)

    def test_a_bill_received_as_an_einvoice_cannot_be_self_billed(self):
        bill = self._bill(post=False)
        inbound = self.env['ncollection.einvoice.inbound'].sudo().create({
            'provider_id': self.env['ncollection.einvoice.provider'].sudo().create(
                {'name': 'P', 'company_id': self.company.id}).id,
            'company_id': self.company.id, 'message_id': 'm', 'asp_document_id': 'd'})
        bill.sudo().write({'nc_inbound_id': inbound.id})
        bill.action_post()
        with self.assertRaisesRegex(UserError, 'cannot be self-billed again'):
            self._einvoice(bill)


@tagged('post_install', '-at_install')
class TestSelfBilledDocuments(SelfBillingCase):

    def test_a_self_billed_tax_invoice_389(self):
        record, xml = self._xml(self._bill())
        profile = spec.get_profile(spec.SELF_BILLING_PROFILE_KEY)
        self.assertTrue(record.self_billing)
        self.assertEqual(record.self_billing_agreement_id, self.agreement)
        self.assertEqual((record.type_code, record.customization_id, record.profile_id),
                         ('389', profile.customization_id, profile.profile_id))
        self.assertEqual(record.spec_version, profile.key)
        # the SUPPLIER is the seller of the document, this company its buyer
        self.assertEqual((record.seller_tin, record.seller_trn), (SUPPLIER_TIN, SUPPLIER_TRN))
        self.assertEqual(record.buyer_tin, self.company.partner_id.nc_tin)
        self.assertIn(b'<cbc:CustomizationID>urn:peppol:pint:selfbilling-1@ae-1</cbc:CustomizationID>', xml)
        self.assertIn(b'<cbc:ProfileID>urn:peppol:bis:selfbilling</cbc:ProfileID>', xml)
        self.assertIn(b'<cbc:InvoiceTypeCode>389</cbc:InvoiceTypeCode>', xml)
        supplier_block = xml.split(b'<cac:AccountingSupplierParty>')[1].split(b'</cac:AccountingSupplierParty>')[0]
        self.assertIn(SUPPLIER_TRN.encode(), supplier_block)
        self.assertIn(b'Gulf Supplies LLC', supplier_block)
        # the payee is the supplier: its own account, never this company's default
        self.assertIn(b'AE070331234567890123456', xml)
        self._export('real-self-billed-tax-invoice-389', xml)

    def test_a_self_billed_tax_credit_note_261_full_reversal(self):
        original = self._bill()
        original_record, _xml = self._xml(original)
        self._validated(original_record)
        refund = self._bill(move_type='in_refund', reversed_entry_id=original.id, invoice_date_due=False)
        record, xml = self._xml(refund, credit_reason='DL8.61.1.E')
        self.assertEqual(record.type_code, '261')
        self.assertIn(b'<cbc:CreditNoteTypeCode>261</cbc:CreditNoteTypeCode>', xml)
        self.assertIn(('<cbc:ID>%s</cbc:ID>' % original.name).encode(), xml.split(b'<cac:BillingReference>')[1])
        self.assertEqual(refund.amount_total, original.amount_total)
        self._export('real-self-billed-tax-credit-note-261', xml)

    def test_a_self_billed_credit_note_needs_its_reason_and_reference(self):
        original = self._bill()
        self._validated(self._ready(original))
        refund = self._bill(move_type='in_refund', reversed_entry_id=original.id)
        self._blocked(refund, 'NCOLLECTION:self-billing-ibr-158-ae')
        unlinked = self._bill(move_type='in_refund')
        self._blocked(unlinked, 'NCOLLECTION:self-billing-ibr-055-ae', credit_reason='DL8.61.1.E')

    def test_a_self_billed_credit_note_cannot_correct_a_standard_invoice_bill(self):
        """A 261 corrects a 389: the original vendor bill must be one this company self-billed."""
        original = self._bill()
        record = self._einvoice(original)  # a self-billed e-invoice that never validated
        record.action_prepare()
        refund = self._bill(move_type='in_refund', reversed_entry_id=original.id)
        self._blocked(refund, 'NCOLLECTION:credit-note-original-not_einvoiced', credit_reason='DL8.61.1.E')

    def test_the_company_default_account_is_never_the_suppliers(self):
        company_bank = self.env['res.partner.bank'].create(
            {'partner_id': self.company.partner_id.id, 'acc_number': 'AE460260001015333439101'}
        )
        self.config.write({'default_payment_means_code': '30', 'default_bank_account_id': company_bank.id})
        # a supplier with NO account of its own: Odoo leaves the bill's bank empty, and the engine must not fall back
        # to this company's default account (the payee of a self-billed document is the supplier)
        bankless = self._partner('Bankless Supplies LLC', tin='1567890128', trn='156789012349003', state='DU',
                                 registry='112345670000003')
        self._agreement(bankless)
        record = self._blocked(self._bill(partner=bankless, bank=False), 'OFFICIAL:ibr-192-ae')
        self.assertNotIn(company_bank.acc_number, record.payload_json or '')

    def test_validation_goes_to_the_self_billing_worker(self):
        record, _xml = self._xml(self._bill())
        seen = {}

        def fake_validate(client, xml, kind, profile=None):
            seen.update(url=client.base_url, profile=profile.key)
            return validator.Outcome(True, 'SaxonC-HE test', profile.pint_version, [], spec.manifest_sha256(profile))

        env = {'NC_EINVOICE_VALIDATOR_URL': 'http://billing:8090', 'NC_EINVOICE_VALIDATOR_SB_URL': 'http://sb:8090',
               'NC_EINVOICE_VALIDATOR_TOKEN': 'x' * 40}
        with patch.dict('os.environ', env), patch.object(validator.HttpValidatorClient, 'validate', fake_validate):
            record.action_validate()
        self.assertEqual(seen, {'url': 'http://sb:8090', 'profile': spec.SELF_BILLING_PROFILE_KEY})
        self.assertEqual(record.local_state, 'validated')

    def test_a_standard_invoice_still_goes_to_the_billing_worker(self):
        record = self._ready(self._invoice())
        record.action_generate_xml()
        seen = {}

        def fake_validate(client, xml, kind, profile=None):
            seen.update(url=client.base_url, profile=profile.key)
            return validator.Outcome(True, 'SaxonC-HE test', profile.pint_version, [], spec.manifest_sha256(profile))

        env = {'NC_EINVOICE_VALIDATOR_URL': 'http://billing:8090', 'NC_EINVOICE_VALIDATOR_SB_URL': 'http://sb:8090',
               'NC_EINVOICE_VALIDATOR_TOKEN': 'x' * 40}
        with patch.dict('os.environ', env), patch.object(validator.HttpValidatorClient, 'validate', fake_validate):
            record.action_validate()
        self.assertEqual(seen, {'url': 'http://billing:8090', 'profile': spec.ACTIVE_PROFILE_KEY})
        self.assertFalse(record.self_billing)

    def test_the_self_billing_rules_are_pinned_and_unedited(self):
        profile = spec.get_profile(spec.SELF_BILLING_PROFILE_KEY)
        self.assertEqual(spec.verify_resources(profile), [])
        self.assertNotEqual(spec.manifest_sha256(profile), spec.manifest_sha256())


@tagged('post_install', '-at_install')
class TestSelfBillingTransport(SelfBillingCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.provider = cls.env['ncollection.einvoice.provider'].sudo().create(
            {'name': 'Fake ASP (SB)', 'company_id': cls.company.id})
        cls.env.cr.execute('UPDATE ncollection_einvoice_provider SET adapter = %s WHERE id = %s',
                           (fake_adapter.FakeAdapter.code, cls.provider.id))
        cls.provider.invalidate_recordset()

    def setUp(self):
        super().setUp()
        self.adapter = fake_adapter.FakeAdapter(self.provider, lambda name: 'cb-secret')
        patcher = patch.object(type(self.env['ncollection.einvoice']), '_nc_adapter', lambda rec, p: self.adapter)
        patcher.start()
        self.addCleanup(patcher.stop)
        captured = self.captured = []
        original_submit = self.adapter.submit

        def submit(request):
            captured.append(request)
            return original_submit(request)
        self.adapter.submit = submit

    def _submitted(self):
        record, _xml = self._xml(self._bill())
        self._validated(record)
        with trap_jobs():
            record.action_submit()
        submission = record.current_submission_id
        with trap_jobs():
            submission._nc_job_submit()
        return record, submission

    def test_a_self_billed_document_uses_the_same_transport_and_says_so(self):
        record, submission = self._submitted()
        self.assertEqual(record.submission_state, 'asp_accepted')
        request = self.captured[0]
        self.assertTrue(request.self_billed)
        self.assertEqual(request.type_code, '389')
        self.assertEqual(request.customization_id, 'urn:peppol:pint:selfbilling-1@ae-1')
        self.assertEqual(request.seller_endpoint, '0235:%s' % SUPPLIER_TIN)
        self.assertTrue(record.locked)

    def test_a_duplicate_submission_is_the_same_submission(self):
        record, submission = self._submitted()
        with self.assertRaises(UserError):
            with trap_jobs():
                record.action_submit()
        self.assertEqual(self.env['ncollection.einvoice.submission'].search_count([('einvoice_id', '=', record.id)]),
                         1)

    def test_a_retry_after_a_transient_failure_keeps_the_key(self):
        record, _xml = self._xml(self._bill())
        self._validated(record)
        self.adapter.fail_next = 'transient'
        with trap_jobs():
            record.action_submit()
        submission = record.current_submission_id
        with trap_jobs():
            submission._nc_job_submit()
        self.assertEqual(submission.state, 'retry_wait')
        submission.sudo().write({'next_attempt_at': fields.Datetime.now() - timedelta(seconds=1)})
        with trap_jobs():
            submission._nc_job_submit()
        self.assertEqual(submission.state, 'done')
        self.assertEqual(len({r.idempotency_key for r in self.captured}), 1)

    def test_mls_and_tdd_confirmations_are_stored_not_generated(self):
        record, submission = self._submitted()
        events = [
            {'kind': 'fta', 'status': 'reported', 'message_id': 'sb-1', 'metadata': {'tdd_reference': 'TDD-SB-1'},
             'correlation_id': submission.correlation_id, 'asp_document_id': submission.asp_document_id,
             'occurred_at': '2026-10-29T10:00:00Z'},
            {'kind': 'mls', 'status': 'AP', 'message_id': 'sb-2', 'metadata': {},
             'correlation_id': submission.correlation_id, 'asp_document_id': submission.asp_document_id,
             'occurred_at': '2026-10-29T10:01:00Z'},
        ]
        body = json.dumps({'events': events}).encode()
        ts, nonce = int(self.adapter.clock()), 'sb-n'
        headers = {'X-Fake-Timestamp': str(ts), 'X-Fake-Nonce': nonce,
                   'X-Fake-Signature': fake_adapter.sign('cb-secret', ts, nonce, body)}
        self.assertTrue(self.adapter.verify_callback(headers, body))
        self.env['ncollection.einvoice.event']._nc_ingest(self.provider, self.adapter.parse_callback(headers, body),
                                                          'callback')
        self.assertEqual((record.fta_state, record.fta_reference, record.mls_state),
                         ('reported', 'TDD-SB-1', 'accepted'))


@tagged('post_install', '-at_install')
class TestReceivedSelfBilled(SelfBillingCase):
    """This company is the SUPPLIER; its customer self-billed it. The received 389 becomes a DRAFT customer invoice."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.provider = cls.env['ncollection.einvoice.provider'].sudo().create(
            {'name': 'Fake ASP (receiving)', 'company_id': cls.company.id})
        cls.env.cr.execute('UPDATE ncollection_einvoice_provider SET adapter = %s WHERE id = %s',
                           (fake_adapter.FakeAdapter.code, cls.provider.id))
        cls.provider.invalidate_recordset()
        cls.sale_partner = cls.supplier  # the customer, as a partner of the receiving company (same identifiers)
        cls.receive_agreement = cls._agreement(cls.sale_partner, direction='receive')

    def setUp(self):
        super().setUp()
        self.adapter = fake_adapter.FakeAdapter(self.provider, lambda name: 'secret')
        patcher = patch.object(type(self.env['ncollection.einvoice']), '_nc_adapter', lambda rec, p: self.adapter)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.profiles = []
        official = patch.object(type(self.env['ncollection.einvoice.inbound']), '_nc_official_check',
                                lambda rec, xml, kind, profile=None: self.profiles.append(profile.key) or [])
        official.start()
        self.addCleanup(official.stop)

    def _self_billed_xml(self, credit=False):
        """A REAL 389 (or 261) built by this module, swapped so that this company is its SELLER (the received side)."""
        if credit:
            original = self._bill()
            self._validated(self._ready(original))
            record, xml = self._xml(self._bill(move_type='in_refund', reversed_entry_id=original.id,
                                               invoice_date_due=False), credit_reason='DL8.61.1.E')
        else:
            record, xml = self._xml(self._bill())
        # issued by cls.company for its supplier: seller = supplier ids, buyer = company ids. For the receiving test
        # the roles are mirrored: this company must be the SELLER, the customer the BUYER.
        ours = self.company.partner_id
        mirrored = xml
        for theirs, mine in ((SUPPLIER_TIN, '#T#'), (SUPPLIER_TRN, '#R#'), ('112345670000002', '#L#')):
            mirrored = mirrored.replace(theirs.encode(), mine.encode())
        for theirs, mine in ((ours.nc_tin, SUPPLIER_TIN), (ours.nc_trn, SUPPLIER_TRN),
                             (ours.company_registry, '112345670000002')):
            mirrored = mirrored.replace(theirs.encode(), mine.encode())
        for placeholder, mine in (('#T#', ours.nc_tin), ('#R#', ours.nc_trn), ('#L#', ours.company_registry)):
            mirrored = mirrored.replace(placeholder.encode(), mine.encode())
        return mirrored

    def _receive(self, xml, document_id='SB-IN-1'):
        self.adapter.receive(document_id, xml)
        with trap_jobs():
            record = self.env['ncollection.einvoice.inbound']._nc_receive(self.provider, document_id)
            record._nc_process()
        return record

    def test_a_received_self_billed_invoice_becomes_a_draft_customer_invoice(self):
        record = self._receive(self._self_billed_xml())
        self.assertEqual(record.state, 'bill_created', record.reason)
        self.assertTrue(record.self_billed)
        self.assertEqual(record.partner_id, self.sale_partner)
        self.assertEqual((record.bill_id.move_type, record.bill_id.state), ('out_invoice', 'draft'))
        self.assertEqual(self.profiles, [spec.SELF_BILLING_PROFILE_KEY], 'validated with the Self-Billing rules')

    def test_a_received_self_billed_credit_note_becomes_a_draft_customer_credit_note(self):
        record = self._receive(self._self_billed_xml(credit=True), 'SB-IN-CN')
        self.assertEqual(record.state, 'bill_created', record.reason)
        self.assertEqual((record.type_code, record.document_kind), ('261', 'credit_note'))
        self.assertEqual((record.bill_id.move_type, record.bill_id.state), ('out_refund', 'draft'))

    def test_nothing_received_is_ever_posted(self):
        invoice = self._receive(self._self_billed_xml(), 'SB-IN-P1')
        credit = self._receive(self._self_billed_xml(credit=True), 'SB-IN-P2')
        self.assertEqual(set((invoice.bill_id | credit.bill_id).mapped('state')), {'draft'})

    def test_without_a_receive_agreement_it_waits_for_review(self):
        self.receive_agreement.action_terminate()
        record = self._receive(self._self_billed_xml(), 'SB-IN-2')
        self.assertEqual(record.state, 'review')
        self.assertEqual(record.reason_code, 'NCOLLECTION:inbound_self_billing_agreement')
        self.assertFalse(record.bill_id)

    def test_the_same_self_billed_document_twice_is_a_duplicate(self):
        xml = self._self_billed_xml()
        first = self._receive(xml, 'SB-IN-3')
        second = self._receive(xml, 'SB-IN-4')
        self.assertEqual(first.state, 'bill_created')
        self.assertEqual((second.state, second.duplicate_of_id), ('duplicate', first))
