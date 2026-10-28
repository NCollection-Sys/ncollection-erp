# -*- coding: utf-8 -*-
"""Second review round: the caller's context, the data frozen after checking, archive accounting, messages.

Odoo fills every field the caller did not give from ``default_<field>`` context keys, and a client sends any context.
The whitelists of the engine inspect the supplied values, so they must also run in a context without those keys."""

import os
from datetime import date
from unittest.mock import patch

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common import validator_env
from .test_hardening import HardeningCase
from .test_prepare import _Worker


@tagged('post_install', '-at_install')
class TestTheCallerContextCannotSteerCreate(HardeningCase):

    def test_default_keys_cannot_forge_state_identity_or_the_archive_link(self):
        move = self._invoice()
        planted = self.env['ir.attachment'].create({'name': 'planted.xml', 'raw': b'<x/>'})
        record = (
            self.env['ncollection.einvoice']
            .with_user(self.user)
            .with_context(
                default_local_state='validated',
                default_locked=True,
                default_submission_state='asp_accepted',
                default_fta_state='reported',
                default_exchange_state='delivered',
                default_uuid='forged-uuid',
                default_payload_json='{}',
                default_xml_attachment_id=planted.id,
                default_xml_sha256='f' * 64,
                default_reconciliation_state='exact',
                default_revision=7,
                default_asp_reference='FORGED',
            )
            .create({'move_id': move.id})
        )
        self.assertEqual(
            (record.local_state, record.submission_state, record.fta_state, record.exchange_state),
            ('draft', 'not_submitted', 'pending', 'pending'),
        )
        self.assertFalse(record.locked)
        self.assertFalse(record.uuid or record.payload_json or record.xml_attachment_id or record.xml_sha256)
        self.assertFalse(record.reconciliation_state or record.asp_reference)
        self.assertEqual(record.revision, 1)
        # and the invoice can still be prepared: nothing was locked behind the user's back
        record.with_user(self.user).action_prepare()
        self.assertEqual(record.local_state, 'ready', self.rules(record))

    def test_the_defaults_a_user_may_type_still_apply(self):
        record = (
            self.env['ncollection.einvoice']
            .with_user(self.user)
            .with_context(default_flag_export=True)
            .create({'move_id': self._invoice().id})
        )
        self.assertTrue(record.flag_export)

    def test_default_keys_cannot_drop_a_file_into_the_archive_or_make_it_public(self):
        record = self._einvoice()
        Attachment = self.env['ir.attachment'].with_user(self.user)
        for context in (
            {'default_res_model': 'ncollection.einvoice', 'default_res_id': record.id},
            {'default_res_model': 'ncollection.einvoice', 'default_res_id': record.id, 'default_public': True},
        ):
            with self.assertRaises(UserError, msg=context):
                Attachment.with_context(**context).create({'name': 'x.xml', 'raw': b'<x/>'})
        self.assertFalse(
            self.env['ir.attachment'].search([('res_model', '=', 'ncollection.einvoice'), ('res_id', '=', record.id)])
        )

    def test_an_archive_file_never_gets_a_token_even_for_the_engine(self):
        attachment = self._with_xml().xml_attachment_id
        with self.assertRaises(UserError):
            attachment.sudo().write({'access_token': 'abc'})
        with self.assertRaises(UserError):
            attachment.sudo().generate_access_token()
        with self.assertRaises(UserError):
            self.env['ir.attachment'].sudo().create(
                {'name': 'y.xml', 'raw': b'<y/>', 'res_model': 'ncollection.einvoice', 'res_id': 1, 'public': True}
            )

    def test_a_provider_cannot_be_created_already_verified(self):
        Provider = self.env['ncollection.einvoice.provider']
        with self.assertRaises(UserError):
            Provider.with_user(self.manager).create(
                {'name': 'ASP', 'company_id': self.company.id, 'state': 'verified'}
            )
        with self.assertRaises(UserError):
            Provider.with_user(self.manager).create(
                {'name': 'ASP', 'company_id': self.company.id, 'asp_participant_id': '9999999999'}
            )
        provider = (
            Provider.with_user(self.manager)
            .with_context(default_state='verified', default_asp_participant_id='9999999999')
            .create({'name': 'ASP', 'company_id': self.company.id})
        )
        self.assertEqual((provider.state, provider.asp_participant_id), ('draft', False))

    def test_a_hold_is_opened_open_by_the_user_who_opens_it(self):
        Hold = self.env['ncollection.einvoice.hold']
        base = {'company_id': self.company.id, 'hold_type': 'tax_audit', 'reference': 'AUD-7'}
        for extra in ({'closed_on': date(2030, 1, 2)}, {'closed_by': self.service.id}, {'opened_by': self.service.id}):
            with self.assertRaises(UserError, msg=extra):
                Hold.with_user(self.manager).create(dict(base, **extra))
        hold = (
            Hold.with_user(self.manager)
            .with_context(
                default_closed_on=date(2030, 1, 2),
                default_opened_by=self.service.id,
                default_closed_by=self.service.id,
            )
            .create(dict(base))
        )
        self.assertFalse(hold.closed_on)
        self.assertFalse(hold.closed_by)
        self.assertEqual(hold.opened_by, self.manager)

    def test_a_hold_cannot_be_closed_before_it_was_opened(self):
        hold = self.env['ncollection.einvoice.hold'].create(
            {
                'company_id': self.company.id,
                'hold_type': 'tax_audit',
                'reference': 'AUD-8',
                'opened_on': date(2030, 5, 1),
            }
        )
        with self.assertRaises(ValidationError):
            hold.write({'closed_on': date(2030, 4, 30)})
        hold.write({'closed_on': date(2030, 5, 1)})


@tagged('post_install', '-at_install')
class TestEditingAfterTheDocumentIsChecked(HardeningCase):

    def test_the_data_a_user_types_is_frozen_once_the_document_is_checked(self):
        record = self._with_xml()
        self.assertEqual(record.local_state, 'locally_checked')
        for vals in (
            {'flag_export': True},
            {'credit_reason': 'VD'},
            {'beneficiary_id': 'B'},
            {'payment_means_override': '10'},
        ):
            with self.assertRaises(UserError, msg=vals):
                record.with_user(self.user).write(vals)
        record.action_prepare()  # back to ready: editable again
        record.with_user(self.user).write({'flag_export': True})
        self.assertTrue(record.flag_export)

    def test_a_retention_change_records_the_old_and_the_new_value(self):
        record = self._einvoice()
        record.with_user(self.manager).write({'retention_class': 'taxable_person'})
        record.with_user(self.manager).write({'retention_class': 'other_person'})
        details = record.history_ids.filtered(lambda h: h.event == 'retention_changed').mapped('detail')
        self.assertEqual(len(details), 2)
        self.assertIn('unclassified', details[0])
        self.assertIn('taxable_person', details[0])
        self.assertIn('taxable_person', details[1])
        self.assertIn('other_person', details[1])


@tagged('post_install', '-at_install')
class TestArchiveAccountingAndMessages(HardeningCase):

    def test_a_record_whose_xml_was_superseded_still_counts_as_regulated(self):
        record = self._with_xml()
        record.sudo().write({'xml_attachment_id': False})  # as between a supersede and the next generate
        status = self.env['ncollection.einvoice']._nc_archive_status()
        self.assertEqual((status['regulated_records'], status['archive_files']), (1, 1))

    def test_a_control_character_gives_a_clear_error_not_a_traceback(self):
        self.product.product_tmpl_id.name = 'Wid\x01get'
        record = self._einvoice()
        record.action_prepare()
        self.assertEqual(record.local_state, 'ready', self.rules(record))
        with self.assertRaises(UserError) as caught:
            record.action_generate_xml()
        self.assertIn('control character', str(caught.exception))
        self.assertFalse(record.xml_attachment_id)

    def test_a_failed_validation_names_the_failed_rules_in_the_history(self):
        worker = _Worker(
            failures=[{'id': 'ibr-190-ae', 'flag': 'fatal', 'location': '/Invoice', 'text': 'rate must be 5'}]
        )
        self.addCleanup(worker.close)
        record = self._with_xml()
        with patch.dict(os.environ, validator_env(worker.url)):
            record.action_validate()
        failed = record.history_ids.filtered(lambda h: h.event == 'official_validation_failed')
        self.assertIn('ibr-190-ae', failed.detail)
        self.assertIn('release', failed.detail)

    def test_the_payee_account_must_belong_to_the_company(self):
        stranger = self.env['res.partner.bank'].create(
            {'acc_number': 'AE070331234567890123456', 'partner_id': self.buyer.id}
        )
        with self.assertRaises(ValidationError):
            self.config.default_bank_account_id = stranger
        own = self.env['res.partner.bank'].create(
            {'acc_number': 'AE070331234567890123457', 'partner_id': self.company.partner_id.id}
        )
        self.config.default_bank_account_id = own
