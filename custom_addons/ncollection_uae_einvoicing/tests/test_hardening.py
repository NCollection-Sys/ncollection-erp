# -*- coding: utf-8 -*-
"""What the pre-merge security, isolation and code reviews found, each as the attack that exposed it.

The tests of the engine run as the superuser (``env.su``), which the engine channel deliberately trusts; every test
here that proves a REFUSAL therefore acts as a real user with ``with_user``."""

import hashlib
import os
from datetime import date
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from ..lib import secret_crypto
from .common import GROUP, EInvoiceCase

USER, MANAGER, SERVICE = (
    GROUP + 'group_einvoice_user',
    GROUP + 'group_einvoice_manager',
    GROUP + 'group_einvoice_service',
)
MASTER = 'a-test-master-key-of-sufficient-length'


class HardeningCase(EInvoiceCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = cls._user('hard_user', [USER])
        cls.manager = cls._user('hard_manager', [MANAGER])
        cls.service = cls._user('hard_service', [SERVICE])

    def _with_xml(self):
        record = self._einvoice()
        record.action_prepare()
        self.assertEqual(record.local_state, 'ready', self.rules(record))
        record.action_generate_xml()
        return record


@tagged('post_install', '-at_install')
class TestTheEngineChannelCannotBeForged(HardeningCase):

    def test_the_context_flag_that_used_to_switch_the_guard_off_does_nothing(self):
        record = self._einvoice()
        attack = record.with_user(self.user).with_context(nc_einvoice_internal=True)
        for vals in (
            {'local_state': 'validated'},
            {'locked': False},
            {'submission_state': 'asp_accepted', 'asp_reference': 'X'},
            {'xml_sha256': 'f' * 64},
        ):
            with self.assertRaises(UserError, msg=vals):
                attack.write(vals)
        self.assertEqual(
            (record.local_state, record.submission_state, record.asp_reference), ('draft', 'not_submitted', False)
        )

    def test_creating_a_record_that_already_claims_a_state_or_an_archive_is_refused(self):
        move = self._invoice()
        Einvoice = self.env['ncollection.einvoice'].with_user(self.user)
        for vals in (
            {'local_state': 'validated'},
            {'locked': True},
            {'submission_state': 'asp_accepted', 'fta_state': 'reported'},
            {'xml_attachment_id': self.env['ir.attachment'].create({'name': 'x', 'raw': b'x'}).id},
            {'xml_sha256': 'a' * 64, 'reconciliation_state': 'exact', 'payload_json': '{}'},
            {'uuid': 'forged', 'name': 'EINV/FORGED'},
            {'document_kind': 'credit_note'},
        ):
            with self.assertRaises(UserError, msg=vals):
                Einvoice.create(dict(vals, move_id=move.id))
        self.assertFalse(self.env['ncollection.einvoice'].search([('move_id', '=', move.id)]))

    def test_the_engine_owned_columns_are_not_editable_by_a_user_even_before_the_lock(self):
        record = self._with_xml()
        other = self.env['ir.attachment'].create({'name': 'other.xml', 'raw': b'<x/>'})
        for vals in (
            {'payload_json': '{}'},
            {'xml_attachment_id': other.id, 'xml_sha256': hashlib.sha256(b'<x/>').hexdigest()},
            {'xml_attachment_id': False},
            {'uuid': 'u'},
            {'invoice_number': 'X'},
            {'seller_tin': '1'},
            {'buyer_trn': '1'},
            {'reconciliation_state': 'exact'},
            {'move_id': self._invoice().id},
            {'company_id': self.company.id},
            {'name': 'EINV/FORGED'},
        ):
            with self.assertRaises(UserError, msg=vals):
                record.with_user(self.user).write(vals)

    def test_what_a_user_may_type_is_still_editable_until_the_document_is_checked(self):
        record = self._einvoice()
        record.with_user(self.user).write({'flag_export': True, 'beneficiary_id': 'B-1', 'billing_frequency': 'MTH'})
        self.assertTrue(record.flag_export)
        self.assertEqual(record.transaction_type_code, '00000001')

    def test_the_retention_class_and_anchor_are_a_managers_and_every_change_is_logged(self):
        record = self._einvoice()
        with self.assertRaises(AccessError):
            record.with_user(self.user).write({'retention_class': 'taxable_person'})
        record.with_user(self.manager).write(
            {'retention_class': 'taxable_person', 'retention_anchor_date': date(2026, 12, 31)}
        )
        logged = record.history_ids.filtered(lambda h: h.event == 'retention_changed')
        self.assertEqual(len(logged), 1)
        self.assertEqual(logged.actor_id, self.manager)

    def test_a_manager_cannot_forge_states_either(self):
        record = self._einvoice()
        with self.assertRaises(UserError):
            record.with_user(self.manager).write({'local_state': 'validated'})


@tagged('post_install', '-at_install')
class TestHistoryAndFindingsAreTheEnginesAlone(HardeningCase):

    def test_a_user_cannot_forge_a_history_row_or_a_finding_or_delete_one(self):
        record = self._einvoice()
        record.action_prepare()
        History = self.env['ncollection.einvoice.history']
        Validation = self.env['ncollection.einvoice.validation']
        with self.assertRaises(AccessError):
            History.with_user(self.user).create({'einvoice_id': record.id, 'event': 'official_validation_passed'})
        with self.assertRaises(AccessError):
            Validation.with_user(self.user).create(
                {'einvoice_id': record.id, 'layer': 'official', 'severity': 'error', 'rule': 'OFFICIAL:x'}
            )
        finding = record.validation_ids[:1] or Validation.sudo().create(
            {'einvoice_id': record.id, 'layer': 'readiness', 'severity': 'error', 'rule': 'NCOLLECTION:x'}
        )
        with self.assertRaises(AccessError):
            finding.with_user(self.user).unlink()
        with self.assertRaises(AccessError):
            finding.with_user(self.manager).unlink()
        # not even the service group writes history directly: the engine does it as sudo
        with self.assertRaises(AccessError):
            History.with_user(self.service).create({'einvoice_id': record.id, 'event': 'x'})

    def test_users_can_read_the_history_and_the_findings(self):
        record = self._einvoice()
        self.assertTrue(record.with_user(self.user).history_ids)

    def test_the_engine_still_writes_both_as_a_user_acting_through_the_actions(self):
        record = self._einvoice()
        record.with_user(self.user).action_prepare()
        self.assertEqual(record.local_state, 'ready', self.rules(record))
        events = record.history_ids.mapped('event')
        self.assertIn('prepared', events)
        self.assertEqual(record.history_ids.filtered(lambda h: h.event == 'prepared').actor_id, self.user)


@tagged('post_install', '-at_install')
class TestTheCompanyIsTheDocumentsCompany(HardeningCase):

    def test_the_active_company_never_decides_whose_e_invoice_it_is(self):
        other = self._company('EInv Active Co', tin='1222222222', trn='122222222202003', registry='2')
        move = self._invoice()
        record = (
            self.env['ncollection.einvoice'].with_context(allowed_company_ids=other.ids).create({'move_id': move.id})
        )
        self.assertEqual(record.company_id, self.company)

    def test_a_different_company_than_the_documents_is_refused(self):
        other = self._company('EInv Other Co 2', tin='1333333333', trn='133333333302003', registry='3')
        with self.assertRaises(UserError):
            self.env['ncollection.einvoice'].create({'move_id': self._invoice().id, 'company_id': other.id})

    def test_the_company_of_an_existing_record_cannot_be_moved(self):
        other = self._company('EInv Third Co', tin='1444444444', trn='144444444402003', registry='4')
        record = self._einvoice()
        with self.assertRaises(UserError):
            record.with_user(self.manager).write({'company_id': other.id})
        with self.assertRaises(ValidationError):
            record.sudo().write({'company_id': other.id})

    def test_a_line_of_another_document_cannot_be_attached(self):
        record = self._einvoice()
        foreign = self._invoice().invoice_line_ids[:1]
        with self.assertRaises(ValidationError):
            self.env['ncollection.einvoice.line'].create({'einvoice_id': record.id, 'move_line_id': foreign.id})


@tagged('post_install', '-at_install')
class TestLinesFollowTheDocumentsState(HardeningCase):

    def test_a_line_toggle_cannot_be_added_or_changed_once_the_document_is_checked(self):
        record = self._with_xml()
        line = record.line_ids[:1]
        with self.assertRaises(UserError):
            line.write({'adjustment': 'allowance'})
        move_line = record.move_id.invoice_line_ids[:1]
        record.sudo().line_ids.unlink()
        with self.assertRaises(UserError):
            self.env['ncollection.einvoice.line'].create({'einvoice_id': record.id, 'move_line_id': move_line.id})

    def test_a_user_changes_only_the_toggle_of_a_line(self):
        record = self._einvoice()
        record.action_prepare()
        line = record.line_ids[:1]
        with self.assertRaises(UserError):
            line.with_user(self.user).write({'move_line_id': self._invoice().invoice_line_ids[:1].id})
        with self.assertRaises(UserError):
            line.with_user(self.user).write({'einvoice_id': self._einvoice().id})
        line.with_user(self.user).write({'reason': 'Because'})


@tagged('post_install', '-at_install')
class TestTheArchiveFileCannotBeExposedOrPolluted(HardeningCase):

    def test_an_archive_file_can_never_be_made_public_or_get_a_token(self):
        attachment = self._with_xml().xml_attachment_id
        for who in (self.user, self.env.user):  # a user, and the superuser
            with self.assertRaises(UserError, msg=who.name):
                attachment.with_user(who).write({'public': True})
        with self.assertRaises(UserError):
            attachment.with_user(self.user).write({'access_token': 'abc'})
        with self.assertRaises(UserError):
            attachment.with_user(self.user).generate_access_token()
        self.assertFalse(attachment.public)

    def test_a_user_cannot_drop_a_file_into_the_archive_or_move_one_in(self):
        record = self._einvoice()
        Attachment = self.env['ir.attachment'].with_user(self.user)
        with self.assertRaises(UserError):
            Attachment.create(
                {'name': 'x.xml', 'raw': b'<x/>', 'res_model': 'ncollection.einvoice', 'res_id': record.id}
            )
        with self.assertRaises(UserError):
            Attachment.create({'name': 'x.xml', 'raw': b'x', 'res_model': 'ncollection.einvoice', 'public': True})
        mine = Attachment.create({'name': 'mine.txt', 'raw': b'x'})
        with self.assertRaises(UserError):
            mine.write({'res_model': 'ncollection.einvoice', 'res_id': record.id})

    def test_the_engine_still_archives(self):
        record = self._with_xml()
        self.assertEqual(record.xml_attachment_id.res_model, 'ncollection.einvoice')
        self.assertFalse(record.xml_attachment_id.public)


@tagged('post_install', '-at_install')
class TestCredentialsAreNotRewritable(HardeningCase):

    def setUp(self):
        super().setUp()
        patcher = patch.dict(os.environ, {secret_crypto.MASTER_ENV: MASTER})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.provider = self.env['ncollection.einvoice.provider'].create(
            {'name': 'Some ASP', 'company_id': self.company.id}
        )
        self.Secret = self.env['ncollection.einvoice.secret']
        self.secret = self.Secret.with_user(self.manager).set_secret(self.provider, 'api_token', 'S3CR3T-VALUE-123')

    def test_a_manager_cannot_create_write_or_re_home_a_secret_directly(self):
        with self.assertRaises(AccessError):
            self.Secret.with_user(self.manager).create(
                {'company_id': self.company.id, 'provider_id': self.provider.id, 'secret_name': 'x'}
            )
        with self.assertRaises(UserError):  # the identity guard answers first; the ACL (AccessError) would too
            self.secret.with_user(self.manager).write({'secret_name': 'other'})
        with self.assertRaises(UserError):
            self.secret.with_user(self.service).write({'key_version': 9})
        with self.assertRaises(AccessError):
            self.secret.with_user(self.manager).write({'ciphertext': 'x'})
        self.assertEqual(self.secret.with_user(self.service)._reveal(), 'S3CR3T-VALUE-123')

    def test_a_secret_belongs_to_the_company_of_its_provider(self):
        other = self._company('EInv Secret Co', tin='1555555555', trn='155555555502003', registry='5')
        with self.assertRaises(ValidationError):
            self.Secret.sudo().create({'company_id': other.id, 'provider_id': self.provider.id, 'secret_name': 'z'})

    def test_the_configured_flag_is_right_in_the_same_transaction(self):
        again = self.Secret.with_user(self.manager).set_secret(self.provider, 'second', 'ANOTHER-VALUE-1234')
        self.assertTrue(again.with_user(self.manager).configured)

    def test_the_master_key_must_be_long_enough(self):
        with patch.dict(os.environ, {secret_crypto.MASTER_ENV: 'x' * (secret_crypto.MASTER_MIN_LENGTH - 1)}):
            with self.assertRaises(secret_crypto.SecretError):
                secret_crypto.master_from_env()
        with patch.dict(os.environ, {secret_crypto.MASTER_ENV: 'x' * secret_crypto.MASTER_MIN_LENGTH}):
            secret_crypto.master_from_env()

    def test_a_name_with_a_delimiter_cannot_collide_with_two_fields(self):
        blob = secret_crypto.encrypt(
            MASTER.encode(), 'value-123456', db='db', company_id=1, provider='2', name='a|3', version=1
        )
        with self.assertRaises(secret_crypto.SecretError):
            secret_crypto.decrypt(MASTER.encode(), blob, db='db', company_id=1, provider='2|a', name='3', version=1)

    def test_the_provider_network_fields_belong_to_the_adapter(self):
        with self.assertRaises(UserError):
            self.provider.with_user(self.manager).write({'asp_participant_id': '9999999999'})
        with self.assertRaises(UserError):
            self.provider.with_user(self.manager).write({'state': 'verified'})
        self.provider.with_user(self.manager).write({'name': 'Renamed ASP'})


@tagged('post_install', '-at_install')
class TestHoldsAndRetentionAreScopedAndFinal(HardeningCase):

    def _rule(self, years, company=None, **extra):
        return (
            self.env['ncollection.einvoice.retention.rule']
            .sudo()
            .create(
                dict(
                    {
                        'retention_class': 'taxable_person',
                        'base_years': years,
                        'base_anchor': 'tax_period_end',
                        'ext_dispute_years': 4,
                        'ext_audit_years': 4,
                        'ext_notification_years': 4,
                        'ext_disclosure_years': 1,
                        'source_citation': 'test citation',
                        'effective_from': date(2020, 1, 1),
                        'confirmed': True,
                        'company_id': company.id if company else False,
                    },
                    **extra
                )
            )
        )

    def _classified(self):
        record = self._einvoice()
        record.write({'retention_class': 'taxable_person', 'retention_anchor_date': date(2026, 12, 31)})
        return record

    def test_another_companys_rule_is_never_applied(self):
        other = self._company('EInv Rule Co', tin='1666666666', trn='166666666602003', registry='6')
        self._rule(99, company=other)
        record = self._classified()
        self.assertFalse(record.retain_until, "the only rule belongs to another company")
        self._rule(5)  # a shared rule
        record.invalidate_recordset(['retain_until'])
        self.assertEqual(record.retain_until, date(2031, 12, 31))
        self._rule(7, company=self.company)
        record.invalidate_recordset(['retain_until'])
        self.assertEqual(record.retain_until, date(2033, 12, 31), "the company's own rule wins over a shared one")

    def test_a_managers_cannot_change_a_rule_shared_by_all_companies(self):
        shared = self._rule(5)
        own = self._rule(5, company=self.company)
        with self.assertRaises(AccessError):
            shared.with_user(self.manager).write({'confirmed': False})
        own.with_user(self.manager).write({'base_years': 6})
        self.assertTrue(shared.with_user(self.manager).read(['base_years']), "shared rules stay readable")

    def test_a_hold_is_closed_not_edited_and_a_closed_hold_is_final(self):
        hold = (
            self.env['ncollection.einvoice.hold']
            .with_user(self.manager)
            .create(
                {
                    'company_id': self.company.id,
                    'hold_type': 'tax_audit',
                    'reference': 'AUD-9',
                    'opened_on': date(2030, 1, 1),
                }
            )
        )
        for vals in ({'hold_type': 'fta_dispute'}, {'reference': 'X'}, {'company_id': self.company.id}):
            with self.assertRaises(UserError, msg=vals):
                hold.with_user(self.manager).write(vals)
        hold.with_user(self.manager).write({'closed_on': date(2031, 1, 1)})
        self.assertEqual(hold.closed_by, self.manager)
        with self.assertRaises(UserError):
            hold.with_user(self.manager).write({'closed_on': date(2020, 1, 1)})
        with self.assertRaises(UserError):
            hold.with_user(self.manager).write({'reason': 'edited after closing'})

    def test_the_archive_status_counts_only_the_users_companies(self):
        self._with_xml()
        other = self._company('EInv Status Co', tin='1777777777', trn='177777777702003', registry='7')
        status = self.env['ncollection.einvoice'].with_context(allowed_company_ids=other.ids)._nc_archive_status()
        self.assertEqual((status['regulated_records'], status['archive_files'], status['open_holds']), (0, 0, 0))


@tagged('post_install', '-at_install')
class TestLicenceAndSwitchAreMirroredAtTheOrm(EInvoiceCase):

    def test_the_licence_check_is_fail_closed_and_names_the_module(self):
        original = self._real_licensed()
        Config = self.env['ncollection.workspace.config']
        Config.search([]).unlink()
        self.assertFalse(original(self.env['ncollection.einvoice']), "no workspace configuration at all")
        Config.create({'allowed_module_names': 'account,crm'})
        self.assertFalse(original(self.env['ncollection.einvoice']), "a licence that does not name the module")
        Config.search([]).write({'allowed_module_names': 'account,ncollection_uae_einvoicing'})
        self.assertTrue(original(self.env['ncollection.einvoice']))

    def _real_licensed(self):
        from ..models.einvoice import EInvoice

        return EInvoice.__dict__['_nc_licensed']

    def test_without_the_licence_nothing_can_be_created_or_run(self):
        move = self._invoice()
        record = self._einvoice(move)
        with patch.object(type(self.env['ncollection.einvoice']), '_nc_licensed', lambda self: False):
            with self.assertRaises(UserError):
                self.env['ncollection.einvoice'].create({'move_id': self._invoice().id})
            for action in ('action_prepare', 'action_generate_xml', 'action_validate'):
                with self.assertRaises(UserError, msg=action):
                    getattr(record, action)()

    def test_a_company_that_has_not_switched_it_on_cannot_prepare(self):
        record = self._einvoice()
        self.config.enabled = False
        with self.assertRaises(UserError):
            record.action_prepare()
        self.config.enabled = True
        record.action_prepare()
        self.assertEqual(record.local_state, 'ready', self.rules(record))


@tagged('post_install', '-at_install')
class TestTheAccountingTruthStaysFinalAfterSubmission(HardeningCase):

    def test_a_submitted_document_cannot_be_reset_or_cancelled(self):
        record = self._validated(self._einvoice())
        record._transition('submission_state', 'queued', 'queued')
        for action in ('button_draft', 'button_cancel'):
            with self.assertRaises(UserError, msg=action):
                getattr(record.move_id, action)()

    def test_before_submission_accounting_is_not_restricted_at_all(self):
        record = self._einvoice()
        record.move_id.button_draft()
        self.assertEqual(record.move_id.state, 'draft')


@tagged('post_install', '-at_install')
class TestMapperGuards(HardeningCase):

    def test_a_cash_rounded_document_is_blocked_with_a_named_rule(self):
        rounding = self.env['account.cash.rounding'].create(
            {'name': 'Cash 0.05', 'rounding': 0.05, 'strategy': 'biggest_tax', 'rounding_method': 'HALF-UP'}
        )
        move = self._invoice(post=False, invoice_cash_rounding_id=rounding.id)
        move.action_post()
        record = self._einvoice(move)
        record.action_prepare()
        self.assertEqual(record.local_state, 'draft')
        self.assertIn('NCOLLECTION:cash-rounding-unsupported', self.rules(record))

    def test_only_a_valid_gtin_is_sent_as_a_gtin(self):
        for barcode, expected in (('4006381333931', '4006381333931'), ('INTERNAL-7', None), ('4006381333932', None)):
            self.product.barcode = barcode
            record = self._einvoice()
            record.action_prepare()
            self.assertEqual(record.payload()['lines'][0]['gtin'], expected, barcode)

    def test_the_payload_does_not_depend_on_the_language_of_whoever_prepares_it(self):
        self.env['res.lang']._activate_lang('ar_001')
        self.product.product_tmpl_id.with_context(lang='ar_001').name = 'ودجت'
        record = self._einvoice()
        record.action_prepare()
        english = record.payload_json
        record.with_context(lang='ar_001').action_prepare()
        self.assertEqual(record.payload_json, english)
        record.with_context(lang='ar_001').action_generate_xml()
        self.assertIn(b'Widget', record.xml_attachment_id.raw)
        self.assertNotIn('ودجت'.encode(), record.xml_attachment_id.raw)

    def test_a_document_in_a_foreign_currency_is_mapped_through_the_real_mapper(self):
        usd = self.env.ref('base.USD')
        usd.active = True
        self.env['res.currency.rate'].create(
            {'currency_id': usd.id, 'company_id': self.company.id, 'name': fields.Date.today(), 'rate': 0.2723}
        )
        move = self._invoice(
            lines=[
                {
                    'product_id': self.product.id,
                    'quantity': 3,
                    'price_unit': 100.0,
                    'tax_ids': [Command.set(self.tax_s.ids)],
                }
            ],
            currency_id=usd.id,
        )
        record = self._einvoice(move)
        record.action_prepare()
        payload = record.payload()
        self.assertEqual(payload['currency'], 'USD')
        self.assertTrue(payload['exchange_rate'])
        self.assertTrue(payload['aed'])
        self.assertEqual(sorted(payload['aed']), ['tax_total', 'total_incl'])
        self.assertNotIn('NCOLLECTION:company-currency-not-aed', self.rules(record))
