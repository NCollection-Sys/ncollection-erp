# -*- coding: utf-8 -*-
"""The ASP adapter CONTRACT SUITE: what every adapter must prove, independent of the provider.

A provider module (``ncollection_uae_einvoicing_<asp>``) reuses it: subclass ``AdapterContractMixin`` together with an
Odoo test case and implement the hooks for its own adapter (with recorded provider answers; never a live call).

Hooks: ``make_adapter()``, ``make_request(key_suffix='')``, ``signed_callback(body)`` -> headers,
``force_transient(adapter)``, ``force_rejection(adapter)``."""

import json
import socket
from unittest.mock import patch

from ..lib import adapter_contract as contract


def _no_network(*_args, **_kwargs):
    raise AssertionError('an adapter contract test opened a network connection')


class AdapterContractMixin:
    # Odoo's test loader collects only methods defined in the class body unless this is set (odoo/tests/loader.py):
    # without it every contract test below would silently not run for a consumer of the mixin
    allow_inherited_tests_method = True

    def setUp(self):
        super().setUp()
        # the contract is proven without a network: any socket connection fails the test
        guard = patch.object(socket.socket, 'connect', _no_network)
        guard.start()
        self.addCleanup(guard.stop)
        self.adapter = self.make_adapter()

    # ---------------------------------------------------------------- submission
    def test_contract_submit_returns_a_provider_document_id_and_echoes_the_identifiers(self):
        request = self.make_request()
        result = self.adapter.submit(request)
        self.assertIsInstance(result, contract.SubmissionResult)
        self.assertEqual(result.status, 'accepted')
        self.assertTrue(result.asp_document_id)
        self.assertEqual(result.correlation_id, request.correlation_id)
        self.assertEqual(result.idempotency_key, request.idempotency_key)
        self.assertIsNone(result.error)

    def test_contract_the_same_idempotency_key_never_creates_a_second_document(self):
        first = self.adapter.submit(self.make_request())
        again = self.adapter.submit(self.make_request())
        self.assertEqual(again.asp_document_id, first.asp_document_id)
        self.assertTrue(again.duplicate)

    def test_contract_a_different_document_gets_a_different_id(self):
        first = self.adapter.submit(self.make_request())
        other = self.adapter.submit(self.make_request(key_suffix='-other'))
        self.assertNotEqual(first.asp_document_id, other.asp_document_id)

    def test_contract_a_transient_failure_is_a_retryable_result_not_an_exception(self):
        self.force_transient(self.adapter)
        result = self.adapter.submit(self.make_request())
        self.assertEqual(result.status, 'failed')
        self.assertTrue(result.retryable)
        self.assertEqual(result.error.category, contract.ErrorCategory.TRANSIENT)
        self.assertIsNone(result.asp_document_id)
        # nothing was stored: the retry with the same key is a FIRST submission
        retry = self.adapter.submit(self.make_request())
        self.assertEqual(retry.status, 'accepted')
        self.assertFalse(retry.duplicate)

    def test_contract_a_rejection_is_not_retryable(self):
        self.force_rejection(self.adapter)
        result = self.adapter.submit(self.make_request())
        self.assertEqual(result.status, 'rejected')
        self.assertFalse(result.retryable)
        self.assertEqual(result.error.category, contract.ErrorCategory.REJECTED)

    # ---------------------------------------------------------------- status
    def test_contract_status_of_a_submitted_document(self):
        request = self.make_request()
        submitted = self.adapter.submit(request)
        status = self.adapter.fetch_status(submitted.asp_document_id, request.correlation_id)
        self.assertIsInstance(status, contract.StatusResult)
        self.assertEqual(status.asp_document_id, submitted.asp_document_id)
        self.assertTrue(all(isinstance(e, contract.CallbackEvent) for e in status.events))

    def test_contract_status_of_an_unknown_document_is_a_classified_error(self):
        with self.assertRaises(contract.AdapterError) as caught:
            self.adapter.fetch_status('does-not-exist')
        self.assertFalse(caught.exception.retryable)

    # ---------------------------------------------------------------- callbacks
    def _callback_body(self, correlation_id='corr-1'):
        return json.dumps(
            {
                'events': [
                    {
                        'kind': 'fta',
                        'status': 'reported',
                        'correlation_id': correlation_id,
                        'asp_document_id': 'X-1',
                        'occurred_at': '2026-10-28T10:00:00Z',
                        'message_id': 'msg-1',
                        'metadata': {'channel': 'tdd'},
                    }
                ]
            }
        ).encode('utf-8')

    def test_contract_an_authentic_callback_verifies_and_parses(self):
        body = self._callback_body()
        headers = self.signed_callback(body)
        self.assertTrue(self.adapter.verify_callback(headers, body))
        events = self.adapter.parse_callback(headers, body)
        self.assertEqual(len(events), 1)
        self.assertEqual((events[0].kind, events[0].status, events[0].correlation_id), ('fta', 'reported', 'corr-1'))
        self.assertEqual(events[0].metadata, {'channel': 'tdd'})
        self.assertTrue(events[0].message_id)

    def test_contract_a_tampered_callback_is_refused(self):
        body = self._callback_body()
        headers = self.signed_callback(body)
        self.assertFalse(self.adapter.verify_callback(headers, body.replace(b'reported', b'rejected')))

    def test_contract_a_replayed_callback_is_refused(self):
        body = self._callback_body()
        headers = self.signed_callback(body)
        self.assertTrue(self.adapter.verify_callback(headers, body))
        self.assertFalse(self.adapter.verify_callback(headers, body))

    def test_contract_an_unsigned_callback_is_refused_without_raising(self):
        self.assertFalse(self.adapter.verify_callback({}, self._callback_body()))

    def test_contract_a_malformed_callback_is_a_classified_error_never_a_crash(self):
        for body in (b'not json', b'{}', b'{"events": [{"kind": "fta"}]}', b'\xff\xfe'):
            with self.assertRaises(contract.AdapterError, msg=body):
                self.adapter.parse_callback({}, body)

    # ---------------------------------------------------------------- classification
    def test_contract_error_classification(self):
        expected = {
            None: contract.ErrorCategory.TRANSIENT,
            408: contract.ErrorCategory.TRANSIENT,
            429: contract.ErrorCategory.TRANSIENT,
            503: contract.ErrorCategory.TRANSIENT,
            401: contract.ErrorCategory.AUTHENTICATION,
            403: contract.ErrorCategory.AUTHENTICATION,
            400: contract.ErrorCategory.REJECTED,
            422: contract.ErrorCategory.REJECTED,
            404: contract.ErrorCategory.PERMANENT,
        }
        for status, category in expected.items():
            self.assertEqual(self.adapter.classify_error(http_status=status).category, category, status)
        self.assertTrue(self.adapter.classify_error(exception=TimeoutError()).retryable)
