# -*- coding: utf-8 -*-
"""A TEST-ONLY adapter: an in-memory ASP that honours the whole contract (lib/adapter_contract.py) without any network.

It is never registered outside the tests and never shipped as a provider. It exists so the contract suite
(tests/adapter_contract.py) has a reference implementation to run against, and so later phases can test the
submission pipeline end to end without a real ASP."""

import hashlib
import hmac
import json
import time

from ..lib import adapter_contract as contract

CALLBACK_WINDOW_SECONDS = 300


def sign(secret, timestamp, nonce, body):
    message = ('%s|%s|%s' % (timestamp, nonce, hashlib.sha256(body).hexdigest())).encode('utf-8')
    return hmac.new(secret.encode('utf-8'), message, hashlib.sha256).hexdigest()


class FakeAdapter(contract.AdapterBase):
    code = 'nc_test_fake'

    def __init__(self, provider, secrets, clock=time.time):
        super().__init__(provider, secrets)
        self.clock = clock
        self.by_key = {}  # idempotency key -> provider document id
        self.documents = {}  # provider document id -> {'correlation_id', 'events'}
        self.seen_nonces = set()
        self.fail_next = None  # None | 'transient' | 'rejected'
        self.calls = 0

    # ---------------------------------------------------------------- outbound
    def submit(self, request):
        if not isinstance(request, contract.SubmissionRequest):
            raise TypeError('submit() takes a SubmissionRequest')
        self.calls += 1
        if self.fail_next == 'transient':
            self.fail_next = None
            return contract.SubmissionResult(
                status='failed',
                correlation_id=request.correlation_id,
                idempotency_key=request.idempotency_key,
                error=self.classify_error(http_status=503),
            )
        if self.fail_next == 'rejected':
            self.fail_next = None
            return contract.SubmissionResult(
                status='rejected',
                correlation_id=request.correlation_id,
                idempotency_key=request.idempotency_key,
                error=self.classify_error(http_status=422, provider_code='FAKE-VALIDATION'),
            )
        if request.idempotency_key in self.by_key:
            return contract.SubmissionResult(
                status='accepted',
                correlation_id=request.correlation_id,
                idempotency_key=request.idempotency_key,
                asp_document_id=self.by_key[request.idempotency_key],
                duplicate=True,
            )
        document_id = 'FAKE-%06d' % (len(self.documents) + 1)
        self.by_key[request.idempotency_key] = document_id
        event = contract.CallbackEvent(
            kind='submission',
            status='accepted',
            correlation_id=request.correlation_id,
            asp_document_id=document_id,
            occurred_at='2026-10-28T00:00:00Z',
            message_id='%s-accepted' % document_id,
        )
        self.documents[document_id] = {'correlation_id': request.correlation_id, 'events': [event]}
        return contract.SubmissionResult(
            status='accepted',
            correlation_id=request.correlation_id,
            idempotency_key=request.idempotency_key,
            asp_document_id=document_id,
            received_at='2026-10-28T00:00:00Z',
            response_sha256=hashlib.sha256(document_id.encode('utf-8')).hexdigest(),
        )

    def fetch_status(self, asp_document_id, correlation_id=None):
        document = self.documents.get(asp_document_id)
        if document is None:
            raise contract.AdapterError(contract.ErrorCategory.PERMANENT, 'not-found', 'unknown document')
        return contract.StatusResult(asp_document_id, document['correlation_id'], tuple(document['events']))

    # ---------------------------------------------------------------- callbacks
    def verify_callback(self, headers, body):
        try:
            timestamp = int(headers.get('X-Fake-Timestamp', ''))
            nonce = headers.get('X-Fake-Nonce') or ''
            signature = headers.get('X-Fake-Signature') or ''
        except (TypeError, ValueError):
            return False
        if not nonce or abs(self.clock() - timestamp) > CALLBACK_WINDOW_SECONDS or nonce in self.seen_nonces:
            return False
        expected = sign(self.secrets('callback_secret'), timestamp, nonce, body)
        if not hmac.compare_digest(expected, signature):
            return False
        self.seen_nonces.add(nonce)
        return True

    def parse_callback(self, headers, body):
        try:
            data = json.loads(body.decode('utf-8'))
            raw_events = data['events']
            events = [
                contract.CallbackEvent(
                    kind=item['kind'],
                    status=item['status'],
                    correlation_id=item.get('correlation_id'),
                    asp_document_id=item.get('asp_document_id'),
                    occurred_at=item.get('occurred_at'),
                    message_id=item['message_id'],
                    metadata=dict(item.get('metadata') or {}),
                    payload_sha256=hashlib.sha256(body).hexdigest(),
                )
                for item in raw_events
            ]
        except (ValueError, KeyError, TypeError, AttributeError, UnicodeDecodeError):
            raise contract.AdapterError(
                contract.ErrorCategory.PERMANENT, 'malformed-callback', 'the callback body is not understood'
            ) from None
        for event in events:
            if event.kind not in contract.EVENT_KINDS:
                raise contract.AdapterError(contract.ErrorCategory.PERMANENT, 'unknown-event', event.kind)
        return events
