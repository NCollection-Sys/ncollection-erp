# -*- coding: utf-8 -*-
"""The transport engine (models/transport.py) end to end, against the in-memory contract adapter
(tests/fake_adapter.py).

Every reliability case the contract promises is exercised on real records: one submission per revision, a duplicate
worker, a timeout AFTER a successful send, retries with backoff and the dead letter, refused credentials, a rejection,
and every callback hazard (forged, replayed, duplicated, reordered, unknown, orphan)."""

import hashlib
import json
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.queue_job.tests.common import trap_jobs

from ..lib import adapter_contract as contract
from ..lib import backoff
from . import fake_adapter
from .common import EInvoiceCase

SECRET = 'callback-secret-for-tests'


class TimeoutAfterSend(fake_adapter.FakeAdapter):
    """The provider receives and stores the document, then the answer is lost (a network timeout)."""

    lose_answer = True

    def submit(self, request):
        result = super().submit(request)
        if self.lose_answer:
            self.lose_answer = False
            return contract.SubmissionResult(status='failed', correlation_id=request.correlation_id,
                                             idempotency_key=request.idempotency_key,
                                             error=self.classify_error(exception=TimeoutError()))
        return result


class TransportCase(EInvoiceCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.provider = cls.env['ncollection.einvoice.provider'].sudo().create(
            {'name': 'Fake ASP', 'company_id': cls.company.id})
        cls.env.cr.execute('UPDATE ncollection_einvoice_provider SET adapter = %s WHERE id = %s',
                           (fake_adapter.FakeAdapter.code, cls.provider.id))
        cls.provider.invalidate_recordset()

    def setUp(self):
        super().setUp()
        self.adapter = self._make_adapter(fake_adapter.FakeAdapter)

    def _make_adapter(self, cls):
        adapter = cls(self.provider, lambda name: SECRET)
        patcher = patch.object(type(self.env['ncollection.einvoice']), '_nc_adapter', lambda rec, provider: adapter)
        patcher.start()
        self.addCleanup(patcher.stop)
        return adapter

    def _validated_einvoice(self):
        record = self._einvoice()
        record.action_prepare()
        record.action_generate_xml()
        return self._validated(record)

    def _submit(self, record):
        with trap_jobs() as trap:
            record.action_submit()
        return record.current_submission_id, trap

    def _run(self, submission):
        with trap_jobs():
            return submission._nc_job_submit()

    def _callback(self, events, nonce='n-1', secret=SECRET, timestamp=None):
        body = json.dumps({'events': events}).encode()
        ts = int(timestamp if timestamp is not None else self.adapter.clock())
        headers = {'X-Fake-Timestamp': str(ts), 'X-Fake-Nonce': nonce,
                   'X-Fake-Signature': fake_adapter.sign(secret, ts, nonce, body)}
        if not self.adapter.verify_callback(headers, body):
            return None
        return self.env['ncollection.einvoice.event']._nc_ingest(
            self.provider, self.adapter.parse_callback(headers, body), 'callback')


@tagged('post_install', '-at_install')
class TestSubmission(TransportCase):

    def test_only_a_validated_einvoice_is_submitted(self):
        record = self._einvoice()
        with self.assertRaisesRegex(UserError, 'officially validated'):
            record.action_submit()

    def test_submit_queues_one_job_and_one_submission(self):
        record = self._validated_einvoice()
        submission, trap = self._submit(record)
        self.assertEqual(record.submission_state, 'queued')
        self.assertEqual(submission.state, 'queued')
        trap.assert_jobs_count(1, only=submission._nc_job_submit)
        self.assertTrue(record.locked, 'leaving not_submitted locks the document')
        with self.assertRaisesRegex(UserError, 'already submitted'):
            record.action_submit()

    def test_the_idempotency_key_is_unique_in_the_database(self):
        record = self._validated_einvoice()
        submission, _trap = self._submit(record)
        with self.assertRaises(Exception), self.env.cr.savepoint():
            self.env['ncollection.einvoice.submission'].sudo().create({
                'einvoice_id': record.id, 'provider_id': self.provider.id, 'revision': record.revision,
                'xml_sha256': record.xml_sha256, 'idempotency_key': submission.idempotency_key,
                'correlation_id': 'another'})

    def test_accepted_records_the_provider_id_and_reaches_asp_accepted(self):
        record = self._validated_einvoice()
        submission, _trap = self._submit(record)
        self.assertEqual(self._run(submission), 'accepted')
        self.assertEqual(submission.state, 'done')
        self.assertEqual(record.submission_state, 'asp_accepted')
        self.assertEqual(record.asp_reference, submission.asp_document_id)
        self.assertEqual(submission.attempt_ids.mapped('outcome'), ['accepted'])
        path = record.history_ids.filtered(lambda h: h.dimension == 'submission_state').mapped('to_state')
        self.assertEqual(path, ['queued', 'submitting', 'submitted', 'asp_accepted'])

    def test_a_second_worker_does_nothing(self):
        record = self._validated_einvoice()
        submission, _trap = self._submit(record)
        self._run(submission)
        self.assertIn('nothing to do', self._run(submission))
        self.assertEqual(self.adapter.calls, 1, 'the provider was called once')

    def test_a_locked_row_is_left_to_the_worker_holding_it(self):
        record = self._validated_einvoice()
        submission, _trap = self._submit(record)
        with patch.object(type(submission), '_nc_lock', lambda self: False):
            self.assertEqual(self._run(submission), 'locked by another worker')
        self.assertEqual(self.adapter.calls, 0)

    def test_a_timeout_after_a_successful_send_creates_no_second_document(self):
        self.adapter = self._make_adapter(TimeoutAfterSend)
        record = self._validated_einvoice()
        submission, _trap = self._submit(record)
        self.assertEqual(self._run(submission), 'retry')
        self.assertEqual(record.submission_state, 'transient_failure')
        submission.sudo().write({'next_attempt_at': fields.Datetime.now() - timedelta(seconds=1)})
        self.assertEqual(self._run(submission), 'accepted')
        self.assertTrue(submission.duplicate_seen, 'the provider recognised the same key')
        self.assertEqual(len(self.adapter.documents), 1, 'ONE legal document at the provider')

    def test_transient_failures_back_off_then_dead_letter(self):
        record = self._validated_einvoice()
        submission, _trap = self._submit(record)
        for number in range(1, backoff.MAX_ATTEMPTS + 1):
            self.adapter.fail_next = 'transient'
            submission.sudo().write({'next_attempt_at': fields.Datetime.now() - timedelta(seconds=1)})
            outcome = self._run(submission)
            if number < backoff.MAX_ATTEMPTS:
                self.assertEqual(outcome, 'retry')
                self.assertGreater(submission.next_attempt_at, fields.Datetime.now())
        self.assertEqual(outcome, 'dead')
        self.assertEqual(submission.state, 'dead')
        self.assertEqual(record.submission_state, 'permanent_failure')
        self.assertEqual(submission.attempt_count, backoff.MAX_ATTEMPTS)

    def test_a_retry_before_its_time_does_nothing(self):
        record = self._validated_einvoice()
        submission, _trap = self._submit(record)
        self.adapter.fail_next = 'transient'
        self._run(submission)
        self.assertIn('nothing to do', self._run(submission))
        self.assertEqual(self.adapter.calls, 1)

    def test_a_rejection_is_final_and_explained(self):
        record = self._validated_einvoice()
        submission, _trap = self._submit(record)
        self.adapter.fail_next = 'rejected'
        self.assertEqual(self._run(submission), 'rejected')
        self.assertEqual(record.submission_state, 'asp_rejected')
        self.assertIn('NCOLLECTION:asp_rejected', self.rules(record))
        self.assertIn('FAKE-VALIDATION', record.validation_ids.filtered(lambda v: v.layer == 'asp').message)

    def test_refused_credentials_wait_for_an_administrator(self):
        record = self._validated_einvoice()
        submission, _trap = self._submit(record)

        def refuse(request):
            raise contract.AdapterError(contract.ErrorCategory.AUTHENTICATION, '401', 'credentials refused', 401)

        self.adapter.submit = refuse
        self.assertEqual(self._run(submission), 'attention')
        self.assertEqual(submission.state, 'attention')
        self.assertFalse(submission.next_attempt_at, 'nothing retries on its own')
        user = self._user('einv_plain_user', ['ncollection_uae_einvoicing.group_einvoice_user'])
        with self.assertRaisesRegex(UserError, 'manager'):
            record.with_user(user).action_retry_submission()
        del self.adapter.submit
        manager = self._user('einv_restart_manager', ['ncollection_uae_einvoicing.group_einvoice_manager'])
        with trap_jobs():
            record.with_user(manager).action_retry_submission()
        self.assertEqual(self._run(submission), 'accepted')

    def test_an_adapter_bug_is_a_transient_failure_not_a_crash(self):
        record = self._validated_einvoice()
        submission, _trap = self._submit(record)
        self.adapter.submit = lambda request: 1 / 0
        self.assertEqual(self._run(submission), 'retry')
        self.assertEqual(submission.error_code, 'ZeroDivisionError')

    def test_an_answer_for_another_submission_is_refused(self):
        record = self._validated_einvoice()
        submission, _trap = self._submit(record)
        self.adapter.submit = lambda request: contract.SubmissionResult(
            status='accepted', correlation_id='someone-else', idempotency_key=request.idempotency_key,
            asp_document_id='X-1')
        self.assertEqual(self._run(submission), 'dead')
        self.assertEqual(submission.error_code, 'correlation-mismatch')
        self.assertFalse(record.asp_reference)

    def test_an_expired_lease_is_sent_again_with_the_same_key(self):
        record = self._validated_einvoice()
        submission, _trap = self._submit(record)
        submission.sudo().write({'state': 'in_flight', 'lease_until': fields.Datetime.now() - timedelta(minutes=1)})
        with trap_jobs() as trap:
            self.env['ncollection.einvoice.submission']._nc_cron_enqueue_due()
        self.assertEqual(submission.state, 'retry_wait')
        trap.assert_jobs_count(1, only=submission._nc_job_submit)

    def test_the_engine_rows_are_not_writable_by_users(self):
        record = self._validated_einvoice()
        submission, _trap = self._submit(record)
        user = self._user('einv_writer', ['ncollection_uae_einvoicing.group_einvoice_manager'])
        with self.assertRaises(UserError):
            submission.with_user(user).write({'state': 'done'})
        with self.assertRaises(UserError):
            submission.with_user(user).unlink()


@tagged('post_install', '-at_install')
class TestEvents(TransportCase):

    def _accepted(self):
        record = self._validated_einvoice()
        submission, _trap = self._submit(record)
        self._run(submission)
        return record, submission

    def _event(self, submission, kind, status, message_id, **metadata):
        return {'kind': kind, 'status': status, 'message_id': message_id, 'metadata': metadata,
                'correlation_id': submission.correlation_id, 'asp_document_id': submission.asp_document_id,
                'occurred_at': '2026-10-28T10:00:00Z'}

    def test_confirmations_land_on_their_own_dimensions(self):
        record, submission = self._accepted()
        self._callback([
            self._event(submission, 'exchange', 'delivered', 'm-1', exchange_reference='EX-1'),
            self._event(submission, 'fta', 'reported', 'm-2', tdd_reference='TDD-77'),
            self._event(submission, 'mls', 'AP', 'm-3'),
        ])
        self.assertEqual(record.exchange_state, 'delivered')
        self.assertEqual(record.fta_state, 'reported')
        self.assertEqual(record.mls_state, 'accepted')
        self.assertEqual(record.business_state, 'unknown', 'MLS and the TDD never touch the business state')
        self.assertEqual((record.exchange_reference, record.fta_reference), ('EX-1', 'TDD-77'))

    def test_a_duplicate_callback_is_recorded_once(self):
        record, submission = self._accepted()
        event = self._event(submission, 'exchange', 'delivered', 'm-dup')
        self._callback([event], nonce='a')
        self._callback([event], nonce='b')
        self.assertEqual(len(record.event_ids.filtered(lambda e: e.message_id == 'm-dup')), 1)

    def test_a_forged_or_replayed_callback_changes_nothing(self):
        record, submission = self._accepted()
        event = self._event(submission, 'exchange', 'delivered', 'm-forged')
        self.assertIsNone(self._callback([event], nonce='f', secret='wrong'))
        self.assertIsNone(self._callback([event], nonce='old', timestamp=self.adapter.clock() - 3600))
        self._callback([self._event(submission, 'mls', 'AB', 'm-ok')], nonce='same')
        self.assertIsNone(self._callback([event], nonce='same'), 'a nonce is used once')
        self.assertEqual(record.exchange_state, 'pending')

    def test_an_event_before_the_acceptance_waits_then_applies(self):
        record = self._validated_einvoice()
        submission, _trap = self._submit(record)
        events = self.env['ncollection.einvoice.event']._nc_ingest(self.provider, [contract.CallbackEvent(
            kind='fta', status='reported', correlation_id=submission.correlation_id, asp_document_id=None,
            occurred_at=None, message_id='early-1')], 'callback')
        self.assertEqual(events.state, 'pending')
        self.assertEqual(record.fta_state, 'pending')
        self._run(submission)
        self.assertEqual(events.state, 'applied')
        self.assertEqual(record.fta_state, 'reported')

    def test_a_report_failure_then_success_settles_and_a_late_failure_is_ignored(self):
        record, submission = self._accepted()
        self._callback([self._event(submission, 'fta', 'report_failed', 'r-1')], nonce='1')
        self._callback([self._event(submission, 'fta', 'reported', 'r-2')], nonce='2')
        late = self._callback([self._event(submission, 'fta', 'report_failed', 'r-3')], nonce='3')
        self.assertEqual(record.fta_state, 'reported')
        self.assertEqual(late.state, 'ignored')

    def test_buyer_responses_move_the_business_state_until_final(self):
        record, submission = self._accepted()
        self._callback([self._event(submission, 'buyer_response', 'IP', 'b-1'),
                        self._event(submission, 'buyer_response', 'AP', 'b-2'),
                        self._event(submission, 'buyer_response', 'PD', 'b-3')], nonce='b')
        self.assertEqual(record.business_state, 'paid')
        late = self._callback([self._event(submission, 'buyer_response', 'RE', 'b-4')], nonce='c')
        self.assertEqual(late.state, 'ignored')
        self.assertEqual(record.business_state, 'paid')

    def test_unknown_and_orphan_events_are_kept_not_applied(self):
        record, submission = self._accepted()
        unknown = self._callback([self._event(submission, 'exchange', 'teleported', 'u-1')], nonce='u')
        self.assertEqual(unknown.state, 'ignored')
        orphan = self.env['ncollection.einvoice.event']._nc_ingest(self.provider, [contract.CallbackEvent(
            kind='exchange', status='delivered', correlation_id='nobody', asp_document_id='NOPE',
            occurred_at=None, message_id='o-1')], 'callback')
        self.assertEqual(orphan.state, 'orphan')
        self.assertFalse(orphan.einvoice_id)

    def test_disagreeing_ids_are_never_guessed(self):
        record, submission = self._accepted()
        event = self.env['ncollection.einvoice.event']._nc_ingest(self.provider, [contract.CallbackEvent(
            kind='exchange', status='delivered', correlation_id=submission.correlation_id,
            asp_document_id='SOMEONE-ELSE', occurred_at=None, message_id='x-1')], 'callback')
        self.assertEqual(event.state, 'orphan')
        self.assertEqual(record.exchange_state, 'pending')

    def test_a_poll_ingests_the_providers_events(self):
        record, submission = self._accepted()
        self.adapter.documents[submission.asp_document_id]['events'].append(contract.CallbackEvent(
            kind='exchange', status='delivered', correlation_id=submission.correlation_id,
            asp_document_id=submission.asp_document_id, occurred_at=None, message_id='p-1'))
        self.assertEqual(submission._nc_job_poll(), 'polled')
        self.assertEqual(record.exchange_state, 'delivered')
        self.assertEqual(submission._nc_job_poll(), 'polled')
        self.assertEqual(len(record.event_ids), 2, 'the acceptance and the delivery, each once')

    def test_the_payload_is_never_stored_only_its_digest(self):
        record, submission = self._accepted()
        stored = self._callback([self._event(submission, 'mls', 'AP', 'd-1')], nonce='d')
        self.assertTrue(stored.payload_sha256)
        self.assertEqual(len(stored.payload_sha256), len(hashlib.sha256(b'').hexdigest()))
