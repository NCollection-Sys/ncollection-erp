# -*- coding: utf-8 -*-
"""Security, tenant/company isolation, credential secrecy, the state machine, immutability, the archive and
retention."""

import os
from datetime import date
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged
from odoo.tools import mute_logger

from ..lib import secret_crypto
from .common import GROUP, EInvoiceCase

USER, MANAGER, SERVICE = (
    GROUP + 'group_einvoice_user',
    GROUP + 'group_einvoice_manager',
    GROUP + 'group_einvoice_service',
)
MASTER = 'a-test-master-key-of-sufficient-length'


@tagged('post_install', '-at_install')
class TestAccessAndIsolation(EInvoiceCase):

    def test_a_user_without_the_groups_cannot_read_any_engine_model(self):
        outsider = self._user('outsider', ['base.group_user'])
        for model in (
            'ncollection.einvoice',
            'ncollection.einvoice.config',
            'ncollection.einvoice.tax.map',
            'ncollection.einvoice.payment.map',
            'ncollection.einvoice.provider',
            'ncollection.einvoice.secret',
            'ncollection.einvoice.history',
            'ncollection.einvoice.validation',
            'ncollection.einvoice.hold',
        ):
            with self.assertRaises(AccessError, msg=model):
                self.env[model].with_user(outsider).search([])

    def test_a_user_reads_but_does_not_configure_and_a_manager_does(self):
        user = self._user('einv_user', [USER])
        manager = self._user('einv_manager', [MANAGER])
        self.assertTrue(self.env['ncollection.einvoice.config'].with_user(user).search([]))
        with self.assertRaises(AccessError):
            self.config.with_user(user).write({'default_payment_means_code': '30'})
        self.config.with_user(manager).write({'default_payment_means_code': '30'})
        with self.assertRaises(AccessError):
            self.env['ncollection.einvoice.tax.map'].with_user(user).create(
                {'company_id': self.company.id, 'tax_id': self.tax_s.id, 'pint_category': 'Z'}
            )

    def test_company_a_never_sees_company_b(self):
        other = self._company('EInv Other Co', tin='1111111111', trn='111111111111103', registry='1')
        user_a = self._user('user_a', [USER])
        record_a = self._einvoice()
        env_b = self.env(context=dict(self.env.context, allowed_company_ids=other.ids))
        move_b = env_b['account.move'].create(
            {
                'move_type': 'out_invoice',
                'partner_id': self.buyer.id,
                'company_id': other.id,
                'invoice_date': fields.Date.today(),
                'invoice_line_ids': [
                    (
                        0,
                        0,
                        {
                            'name': 'x',
                            'quantity': 1,
                            'price_unit': 10.0,
                            'tax_ids': [
                                (
                                    6,
                                    0,
                                    env_b['account.tax']
                                    .search(
                                        [
                                            ('company_id', '=', other.id),
                                            ('type_tax_use', '=', 'sale'),
                                            ('name', '=', '5% DU'),
                                        ],
                                        limit=1,
                                    )
                                    .ids,
                                )
                            ],
                        },
                    )
                ],
            }
        )
        move_b.action_post()
        record_b = env_b['ncollection.einvoice'].create({'move_id': move_b.id, 'company_id': other.id})
        seen = self.env['ncollection.einvoice'].with_user(user_a).search([])
        self.assertIn(record_a, seen)
        self.assertNotIn(record_b, seen, "another company's e-invoice must be invisible")
        with self.assertRaises(AccessError):
            record_b.with_user(user_a).read(['name'])
        self.assertFalse(
            self.env['ncollection.einvoice.tax.map'].with_user(user_a).search([('company_id', '=', other.id)])
        )

    def test_every_model_has_a_company_record_rule(self):
        models_ = (
            'config',
            'provider',
            'secret',
            'tax.map',
            'payment.map',
            'line',
            'history',
            'validation',
            'hold',
            'retention.rule',
        )
        for suffix in models_ + ('',):
            name = 'ncollection.einvoice' + ('.' + suffix if suffix else '')
            rules = self.env['ir.rule'].search([('model_id.model', '=', name)])
            self.assertTrue(rules, "%s has no record rule" % name)


@tagged('post_install', '-at_install')
class TestOnlyCustomerDocuments(EInvoiceCase):

    def test_a_vendor_bill_is_an_e_invoice_only_under_a_self_billing_agreement(self):
        """Since Cycle 3 a vendor bill can be e-invoiced, but ONLY as a self-billed document (389 / 261) under an
        active agreement with that supplier (tests/test_self_billing.py); without one it is refused at the ORM."""
        bill = self.env['account.move'].create(
            {
                'move_type': 'in_invoice',
                'partner_id': self.buyer.id,
                'company_id': self.company.id,
                'invoice_date': fields.Date.today(),
                'invoice_line_ids': [(0, 0, {'name': 'x', 'quantity': 1, 'price_unit': 10.0})],
            }
        )
        bill.action_post()
        with self.assertRaises(UserError) as caught:
            self.env['ncollection.einvoice'].create({'move_id': bill.id, 'company_id': self.company.id})
        self.assertIn('self-billing agreement', str(caught.exception).lower())

    def test_a_draft_document_cannot_be_prepared(self):
        with self.assertRaises(UserError):
            self._einvoice(self._invoice(post=False))

    def test_the_model_reserves_but_does_not_implement_inbound(self):
        self.assertEqual(
            [c for c, _l in self.env['ncollection.einvoice']._fields['direction'].selection], ['outbound']
        )

    def test_a_revision_is_unique_per_document(self):
        move = self._invoice()
        self._einvoice(move)
        with mute_logger('odoo.sql_db'), self.assertRaises(Exception):
            with self.cr.savepoint():
                self._einvoice(move)


@tagged('post_install', '-at_install')
class TestCredentialSecrecy(EInvoiceCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.provider = cls.env['ncollection.einvoice.provider'].create(
            {'name': 'Some ASP', 'company_id': cls.company.id}
        )
        cls.manager = cls._user('sec_manager', [MANAGER])
        cls.service = cls._user('sec_service', [SERVICE])
        cls.user = cls._user('sec_user', [USER])

    def setUp(self):
        super().setUp()
        patcher = patch.dict(os.environ, {secret_crypto.MASTER_ENV: MASTER})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.Secret = self.env['ncollection.einvoice.secret']

    def test_a_manager_sets_a_secret_and_only_sees_that_it_is_configured(self):
        record = self.Secret.with_user(self.manager).set_secret(self.provider, 'api_token', 'S3CR3T-VALUE-123')
        self.assertTrue(record.with_user(self.manager).configured)
        self.assertEqual(record.set_by, self.manager)
        with self.assertRaises(AccessError):
            record.with_user(self.manager).ciphertext
        with self.assertRaises(AccessError):
            record.with_user(self.manager).read(['ciphertext'])
        with self.assertRaises(AccessError):
            self.Secret.with_user(self.manager).search_read([], ['ciphertext'])
        with self.assertRaises(UserError):  # AccessError is a UserError
            record.with_user(self.manager).export_data(['ciphertext'])

    def test_a_non_manager_cannot_set_a_secret(self):
        for who in (self.user, self.service):
            with self.assertRaises(AccessError):
                self.Secret.with_user(who).set_secret(self.provider, 'api_token', 'x' * 20)

    def test_only_the_service_account_can_decrypt_and_it_round_trips(self):
        record = self.Secret.with_user(self.manager).set_secret(self.provider, 'api_token', 'S3CR3T-VALUE-123')
        self.assertEqual(record.with_user(self.service)._reveal(), 'S3CR3T-VALUE-123')
        for who in (self.manager, self.user):
            with self.assertRaises(AccessError):
                record.with_user(who)._reveal()

    def test_the_stored_value_is_not_the_secret_and_not_a_guessable_function_of_it(self):
        first = self.Secret.with_user(self.manager).set_secret(self.provider, 'a', 'S3CR3T-VALUE-123')
        second = self.Secret.with_user(self.manager).set_secret(self.provider, 'b', 'S3CR3T-VALUE-123')
        blob_a, blob_b = first.with_user(self.service).ciphertext, second.with_user(self.service).ciphertext
        self.assertNotIn('S3CR3T', blob_a)
        self.assertNotEqual(blob_a, blob_b, "same plaintext, different ciphertext (random nonce, bound context)")

    def test_a_copied_ciphertext_cannot_be_decrypted_in_another_context(self):
        record = self.Secret.with_user(self.manager).set_secret(self.provider, 'api_token', 'S3CR3T-VALUE-123')
        blob = record.with_user(self.service).ciphertext
        kwargs = dict(
            db=self.env.cr.dbname, company_id=self.company.id, provider=str(self.provider.id), name='api_token'
        )
        master = secret_crypto.master_from_env()
        self.assertEqual(secret_crypto.decrypt(master, blob, **kwargs), 'S3CR3T-VALUE-123')
        for changed in (
            {'db': 'another_tenant'},
            {'company_id': self.company.id + 1},
            {'provider': '999'},
            {'name': 'other'},
            {'version': 2},
        ):
            with self.assertRaises(secret_crypto.SecretError, msg=changed):
                secret_crypto.decrypt(master, blob, **dict(kwargs, **changed))
        with self.assertRaises(secret_crypto.SecretError):
            secret_crypto.decrypt(b'a-different-master-key-value', blob, **kwargs)

    def test_without_the_master_key_nothing_works_and_nothing_falls_back_to_plaintext(self):
        with patch.dict(os.environ, {secret_crypto.MASTER_ENV: ''}):
            with self.assertRaises(UserError):
                self.Secret.with_user(self.manager).set_secret(self.provider, 'api_token', 'S3CR3T-VALUE-123')
        self.assertFalse(self.Secret.search([('provider_id', '=', self.provider.id)]))

    def test_errors_never_contain_the_secret_or_the_master(self):
        record = self.Secret.with_user(self.manager).set_secret(self.provider, 'api_token', 'S3CR3T-VALUE-123')
        with patch.dict(os.environ, {secret_crypto.MASTER_ENV: 'another-master-key-of-enough-length'}):
            with self.assertRaises(UserError) as caught:
                record.with_user(self.service)._reveal()
        text = str(caught.exception)
        self.assertNotIn('S3CR3T', text)
        self.assertNotIn(MASTER, text)

    def test_the_secret_never_reaches_logs_or_the_history(self):
        with self.assertLogs(level='DEBUG') as logs:
            self.Secret.with_user(self.manager).set_secret(self.provider, 'api_token', 'S3CR3T-VALUE-123')
            import logging

            logging.getLogger('odoo.test').debug('marker')
        self.assertFalse([line for line in logs.output if 'S3CR3T' in line])
        self.assertFalse(self.env['ncollection.einvoice.history'].search([('detail', 'ilike', 'S3CR3T')]))

    def test_an_empty_secret_is_refused(self):
        for empty in ('', '   ', None):
            with self.assertRaises(UserError):
                self.Secret.with_user(self.manager).set_secret(self.provider, 'api_token', empty)

    def test_replacing_a_secret_keeps_one_row(self):
        self.Secret.with_user(self.manager).set_secret(self.provider, 'api_token', 'FIRST-VALUE-1234')
        again = self.Secret.with_user(self.manager).set_secret(self.provider, 'api_token', 'SECOND-VALUE-123')
        self.assertEqual(self.Secret.search_count([('provider_id', '=', self.provider.id)]), 1)
        self.assertEqual(again.with_user(self.service)._reveal(), 'SECOND-VALUE-123')


@tagged('post_install', '-at_install')
class TestStateMachineAndImmutability(EInvoiceCase):

    def test_the_five_state_dimensions_start_where_they_should(self):
        record = self._einvoice()
        self.assertEqual(
            (
                record.local_state,
                record.submission_state,
                record.exchange_state,
                record.fta_state,
                record.business_state,
            ),
            ('draft', 'not_submitted', 'pending', 'pending', 'unknown'),
        )
        self.assertEqual(record.headline, 'Draft')

    def test_legal_transitions_move_and_leave_history(self):
        record = self._einvoice()
        record._transition('local_state', 'ready', 'prepared')
        record._transition('local_state', 'locally_checked', 'xml_generated')
        events = record.history_ids.filtered(lambda h: h.dimension == 'local_state')
        self.assertEqual(
            [(h.from_state, h.to_state, h.event) for h in events],
            [('draft', 'ready', 'prepared'), ('ready', 'locally_checked', 'xml_generated')],
        )
        self.assertTrue(all(h.actor_id and h.at for h in events))

    def test_illegal_transitions_are_refused(self):
        record = self._einvoice()
        for dimension, state in (
            ('local_state', 'validated'),
            ('submission_state', 'asp_accepted'),
            ('fta_state', 'bogus'),
        ):
            with self.assertRaises(UserError, msg=(dimension, state)):
                record._transition(dimension, state, 'x')
        self._validated(record)
        record._transition('submission_state', 'queued', 'queued')
        with self.assertRaises(UserError):
            record._transition('submission_state', 'asp_accepted', 'skipped submitting')

    def test_a_document_that_is_not_validated_cannot_leave_not_submitted(self):
        record = self._einvoice()
        for state in ('draft', 'ready', 'locally_checked', 'validation_failed'):
            record.write({'local_state': state})
            with self.assertRaises(UserError, msg=state):
                record._transition('submission_state', 'queued', 'queued')
        self.assertFalse(record.locked)

    def test_states_are_independent_dimensions(self):
        record = self._validated(self._einvoice())
        record._transition('submission_state', 'queued', 'queued')
        self.assertEqual(record.local_state, 'validated', "a queue move says nothing about local validation")
        self.assertEqual(record.fta_state, 'pending')
        self.assertEqual(record.headline, 'Queued')

    def test_terminal_states_have_no_exit(self):
        record = self._validated(self._einvoice())
        for state in ('queued', 'submitting', 'submitted', 'asp_accepted'):
            record._transition('submission_state', state, state)
        for state in ('queued', 'asp_rejected', 'submitted'):
            with self.assertRaises(UserError):
                record._transition('submission_state', state, 'x')

    def test_states_cannot_be_written_directly(self):
        record = self._einvoice()
        user = self._user('imm_state_user', [USER])
        for field, value in (('local_state', 'validated'), ('submission_state', 'asp_accepted'), ('locked', True)):
            with self.assertRaises(UserError, msg=field):
                record.with_user(user).write({field: value})
        self.assertEqual(
            (record.local_state, record.submission_state, record.locked), ('draft', 'not_submitted', False)
        )

    def test_network_references_are_written_only_by_the_service(self):
        record = self._einvoice()
        manager = self._user('imm_manager', [MANAGER])
        service = self._user('imm_service', [SERVICE])
        with self.assertRaises(UserError):
            record.with_user(manager).write({'asp_reference': 'ASP-1'})
        record.with_user(service).write({'asp_reference': 'ASP-1', 'fta_reference': 'FTA-1'})
        self.assertEqual((record.asp_reference, record.fta_reference), ('ASP-1', 'FTA-1'))

    def test_leaving_not_submitted_locks_the_content(self):
        record = self._validated(self._einvoice())
        record.write({'payload_json': '{}', 'uuid': 'u-1'})
        record.write({'beneficiary_id': 'B1'})  # still editable
        record._transition('submission_state', 'queued', 'queued')
        self.assertTrue(record.locked)
        for field, value in (
            ('payload_json', '{"x": 1}'),
            ('uuid', 'u-2'),
            ('invoice_number', 'X'),
            ('seller_tin', '1'),
        ):
            with self.assertRaises(UserError, msg=field):
                record.write({field: value})
        for field, value in (('beneficiary_id', 'B2'), ('flag_export', True), ('billing_frequency', 'MTH')):
            with self.assertRaises(UserError, msg=field):
                record.write({field: value})
        self.assertEqual(record.uuid, 'u-1')

    def test_a_locked_document_cannot_be_prepared_again(self):
        record = self._validated(self._einvoice())
        record._transition('submission_state', 'queued', 'queued')
        with self.assertRaises(UserError):
            record.action_prepare()

    def test_history_is_append_only_for_everyone(self):
        record = self._einvoice()
        entry = record.history_ids[0]
        for call in (
            lambda: entry.write({'detail': 'edited'}),
            entry.unlink,
            entry.sudo().unlink,
            lambda: entry.sudo().write({'event': 'x'}),
        ):
            with self.assertRaises(UserError):
                call()

    def test_an_e_invoice_record_is_never_deleted(self):
        for prepare in (False, True):
            record = self._einvoice()
            if prepare:
                record._transition('local_state', 'ready', 'prepared')
            with self.assertRaises(UserError):
                record.unlink()
            with self.assertRaises(UserError):
                record.sudo().unlink()
        queued = self._validated(self._einvoice())
        queued._transition('submission_state', 'queued', 'queued')
        with self.assertRaises(UserError):
            queued.unlink()

    def test_identifiers_are_separate_fields_that_never_alias(self):
        record = self._einvoice()
        record.action_prepare()
        values = {
            'odoo number': record.move_id.name,
            'record id': record.name,
            'pint number': record.invoice_number,
            'uuid': record.uuid,
            'seller tin': record.seller_tin,
            'seller trn': record.seller_trn,
            'endpoint': record.seller_endpoint_id,
            'participant id': record.asp_participant_id,
            'asp reference': record.asp_reference,
            'exchange reference': record.exchange_reference,
            'fta reference': record.fta_reference,
        }
        present = {k: v for k, v in values.items() if v}
        # the only legitimate equalities: the PINT number IS a snapshot of the Odoo number; the endpoint IS the TIN
        self.assertEqual(values['pint number'], values['odoo number'])
        self.assertEqual(values['endpoint'], values['seller tin'])
        distinct = {k: v for k, v in present.items() if k not in ('pint number', 'endpoint')}
        self.assertEqual(len(set(distinct.values())), len(distinct), "an identifier must never stand in for another")
        for network in ('participant id', 'asp reference', 'exchange reference', 'fta reference'):
            self.assertFalse(values[network], "%s is written by an adapter result only" % network)
        self.assertNotEqual(record.name, record.move_id.name)


@tagged('post_install', '-at_install')
class TestArchiveAndRetention(EInvoiceCase):

    def _with_xml(self):
        record = self._einvoice()
        record.action_prepare()
        self.assertEqual(record.local_state, 'ready', self.rules(record))
        record.action_generate_xml()
        return record

    def test_the_xml_is_archived_with_its_checksum_and_cannot_be_altered_or_deleted(self):
        record = self._with_xml()
        attachment = record.xml_attachment_id
        import hashlib

        self.assertEqual(record.xml_sha256, hashlib.sha256(attachment.raw).hexdigest())
        self.assertEqual((attachment.res_model, attachment.res_id), ('ncollection.einvoice', record.id))
        for call in (
            lambda: attachment.write({'raw': b'<x/>'}),
            lambda: attachment.write({'name': 'other.xml'}),
            lambda: attachment.write({'res_id': 0}),
            attachment.unlink,
            attachment.sudo().unlink,
            lambda: attachment.sudo().write({'datas': 'eA=='}),
        ):
            with self.assertRaises(UserError):
                call()
        self.assertEqual(hashlib.sha256(attachment.raw).hexdigest(), record.xml_sha256)

    def test_a_record_with_archived_xml_cannot_be_deleted(self):  # kept: the XML case, explicitly
        record = self._with_xml()
        with self.assertRaises(UserError):
            record.unlink()

    def test_the_archive_status_for_offboarding_is_counts_only(self):
        self._with_xml()
        status = self.env['ncollection.einvoice']._nc_archive_status()
        self.assertEqual(
            set(status),
            {
                'regulated_records',
                'archive_files',
                'purge_eligible',
                'unclassified_or_unconfirmed',
                'earliest_retain_until',
                'open_holds',
            },
        )
        self.assertEqual(status['regulated_records'], 1)
        self.assertEqual((status['purge_eligible'], status['unclassified_or_unconfirmed']), (0, 1))
        for value in status.values():
            self.assertIsInstance(value, (int, bool, str))

    def test_a_record_is_never_purgeable_without_a_confirmed_rule(self):
        record = self._with_xml()
        self.assertFalse(record.retain_until, "unclassified")
        record.write({'retention_class': 'taxable_person', 'retention_anchor_date': date(2026, 12, 31)})
        self.assertFalse(record.retain_until, "classified but no rule at all")
        self.assertFalse(record.retain_until)
        rule = self.env['ncollection.einvoice.retention.rule'].create(
            {
                'retention_class': 'taxable_person',
                'base_years': 5,
                'base_anchor': 'tax_period_end',
                'ext_dispute_years': 4,
                'ext_audit_years': 4,
                'ext_notification_years': 4,
                'ext_disclosure_years': 1,
                'source_citation': 'test citation',
                'effective_from': date(2020, 1, 1),
                'confirmed': False,
            }
        )
        record.invalidate_recordset(['retain_until'])
        self.assertFalse(record.retain_until, "an UNCONFIRMED rule never counts")
        rule.confirmed = True
        record.invalidate_recordset(['retain_until'])
        self.assertEqual(record.retain_until, date(2031, 12, 31))

    def test_holds_block_and_extend_retention_as_the_confirmed_rule_says(self):
        record = self._with_xml()
        record.write({'retention_class': 'taxable_person', 'retention_anchor_date': date(2026, 12, 31)})
        self.env['ncollection.einvoice.retention.rule'].create(
            {
                'retention_class': 'taxable_person',
                'base_years': 5,
                'base_anchor': 'tax_period_end',
                'ext_dispute_years': 4,
                'ext_audit_years': 4,
                'ext_notification_years': 4,
                'ext_disclosure_years': 1,
                'source_citation': 'test citation',
                'effective_from': date(2020, 1, 1),
                'confirmed': True,
            }
        )
        hold = self.env['ncollection.einvoice.hold'].create(
            {
                'company_id': self.company.id,
                'hold_type': 'tax_audit',
                'reference': 'AUD-1',
                'opened_on': date(2030, 1, 1),
            }
        )
        record.invalidate_recordset(['retain_until'])
        self.assertFalse(record.retain_until, "an OPEN hold: indefinite, not purgeable")
        hold.closed_on = date(2033, 6, 30)
        record.invalidate_recordset(['retain_until'])
        self.assertEqual(record.retain_until, date(2037, 6, 30), "closure + the audit extension")
        other = self._einvoice()
        self.assertFalse(other.retain_until)

    def test_a_hold_cannot_be_deleted(self):
        hold = self.env['ncollection.einvoice.hold'].create(
            {'company_id': self.company.id, 'hold_type': 'fta_dispute', 'reference': 'D-1'}
        )
        with self.assertRaises(UserError):
            hold.unlink()
