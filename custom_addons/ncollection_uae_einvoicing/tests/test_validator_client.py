# -*- coding: utf-8 -*-
"""The validator boundary: the official-rule worker is out of process, and the engine FAILS CLOSED.

A document becomes ``validated`` only on a well-formed answer from a worker that ran exactly the pinned release and
rule set. Everything else (unreachable, timeout, HTTP error, malformed, other release, other rules, oversized,
redirect) is
"unavailable", never "valid"."""

import base64
import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from odoo.tests import TransactionCase, tagged

from ..lib import issues as _issues, spec, validator
from . import payloads
from ..lib.xml_builder import build
from .common import VALIDATOR_TOKEN, signed_answer


class _Stub:
    """A tiny HTTP server whose next answer is scripted by the test."""

    def __init__(self):
        self.answer = (200, {})
        self.raw = None
        self.seen = []
        self.redirect_to = None
        self.sign = True
        self.forge = None  # a callable (signed answer, request) -> what the (forged) worker really sends
        self.headers_seen = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get('content-length') or 0))
                stub.seen.append(json.loads(body))
                stub.headers_seen.append(self.headers.get('authorization'))
                if stub.redirect_to:
                    self.send_response(302)
                    self.send_header('location', stub.redirect_to)
                    self.end_headers()
                    return
                status, data = stub.answer
                if stub.raw is None and isinstance(data, dict) and 'failures' in data and stub.sign:
                    data = signed_answer(data, stub.seen[-1])
                    if stub.forge:
                        data = stub.forge(data, stub.seen[-1])
                payload = stub.raw if stub.raw is not None else json.dumps(data).encode('utf-8')
                self.send_response(status)
                self.send_header('content-type', 'application/json')
                self.send_header('content-length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.url = 'http://127.0.0.1:%d' % self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def good(self, failures=()):
        profile = spec.get_profile()
        self.answer = (
            200,
            {
                'engine': 'SaxonC-HE test',
                'spec_version': profile.pint_version,
                'manifest_sha256': spec.manifest_sha256(profile),
                'failures': list(failures),
            },
        )


@tagged('post_install', '-at_install')
class TestValidatorClient(TransactionCase):

    def setUp(self):
        super().setUp()
        self.stub = _Stub()
        self.addCleanup(self.stub.close)
        self.client = validator.HttpValidatorClient(self.stub.url, timeout=3, token=VALIDATOR_TOKEN)
        self.xml = build(payloads.standard_payload())

    def test_a_clean_answer_from_the_pinned_rules_is_ok(self):
        self.stub.good()
        outcome = self.client.validate(self.xml, 'invoice')
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.failures, [])

    def test_the_request_carries_the_exact_bytes_the_kind_and_the_release(self):
        self.stub.good()
        self.client.validate(self.xml, 'invoice')
        sent = self.stub.seen[0]
        self.assertEqual(base64.b64decode(sent['xml_b64']), self.xml)
        self.assertEqual((sent['kind'], sent['spec_version']), ('invoice', spec.get_profile().pint_version))

    def test_failures_become_official_issues_and_make_the_outcome_not_ok(self):
        self.stub.good(
            [
                {'id': 'ibr-190-ae', 'flag': 'fatal', 'location': '/Invoice', 'text': 'rate must be 5'},
                {'id': 'ibr-148-ae', 'flag': 'warning', 'location': '/Invoice', 'text': 'should be 1X'},
            ]
        )
        outcome = self.client.validate(self.xml, 'invoice')
        self.assertFalse(outcome.ok)
        found = validator.to_issues(outcome)
        self.assertEqual(
            {(i.rule, i.severity) for i in found},
            {('OFFICIAL:ibr-190-ae', _issues.ERROR), ('OFFICIAL:ibr-148-ae', _issues.WARNING)},
        )

    def test_only_warnings_still_validates(self):
        self.stub.good([{'id': 'ibr-148-ae', 'flag': 'warning', 'location': '', 'text': 'x'}])
        self.assertTrue(self.client.validate(self.xml, 'invoice').ok)

    def test_a_worker_running_another_release_is_unavailable(self):
        self.stub.answer = (
            200,
            {'engine': 'x', 'spec_version': '9.9.9', 'failures': [], 'manifest_sha256': spec.manifest_sha256()},
        )
        with self.assertRaises(validator.ValidatorUnavailable):
            self.client.validate(self.xml, 'invoice')

    def test_a_worker_running_other_rules_is_unavailable(self):
        self.stub.answer = (
            200,
            {
                'engine': 'x',
                'spec_version': spec.get_profile().pint_version,
                'failures': [],
                'manifest_sha256': '0' * 64,
            },
        )
        with self.assertRaises(validator.ValidatorUnavailable):
            self.client.validate(self.xml, 'invoice')

    def test_malformed_and_error_answers_are_unavailable_never_valid(self):
        for status, raw in (
            (200, b'not json'),
            (200, b'{}'),
            (200, b'[]'),
            (200, b'{"failures": 3}'),
            (500, b'{"error": "boom"}'),
            (400, b'{}'),
        ):
            self.stub.answer, self.stub.raw = (status, {}), raw
            with self.assertRaises(validator.ValidatorUnavailable, msg=(status, raw)):
                self.client.validate(self.xml, 'invoice')

    def test_an_unreachable_worker_is_unavailable(self):
        client = validator.HttpValidatorClient('http://127.0.0.1:9', timeout=1, token=VALIDATOR_TOKEN)
        with self.assertRaises(validator.ValidatorUnavailable):
            client.validate(self.xml, 'invoice')

    def test_an_unconfigured_worker_is_unavailable(self):
        for url in ('', None, 'ftp://x', 'localhost:8090'):
            with self.assertRaises(validator.ValidatorUnavailable, msg=url):
                validator.HttpValidatorClient(url, token=VALIDATOR_TOKEN)

    def test_no_token_or_a_short_token_means_unavailable_and_nothing_is_sent(self):
        for token in (None, '', 'short', 'x' * (validator.TOKEN_MIN_LENGTH - 1)):
            with self.assertRaises(validator.ValidatorUnavailable, msg=token):
                validator.HttpValidatorClient(self.stub.url, token=token)
        self.assertEqual(self.stub.seen, [])

    def test_the_request_carries_the_bearer_token(self):
        self.stub.good()
        self.client.validate(self.xml, 'invoice')
        # the bearer is a one-way derivative: the shared token itself (the signing key's source) never crosses the wire
        self.assertEqual(self.stub.headers_seen, ['Bearer ' + validator.auth_secret(VALIDATOR_TOKEN)])
        self.assertNotIn(VALIDATOR_TOKEN, self.stub.headers_seen[0])

    def test_an_unsigned_or_wrongly_signed_answer_is_unavailable(self):
        self.stub.good()
        self.stub.sign = False
        for extra in ({}, {'signature': 'f' * 64}, {'xml_sha256': 'a' * 64, 'signature': 'f' * 64}):
            self.stub.answer = (200, dict(self.stub.answer[1], **extra))
            with self.assertRaises(validator.ValidatorUnavailable, msg=extra):
                self.client.validate(self.xml, 'invoice')

    def test_an_answer_signed_by_someone_without_the_token_is_unavailable(self):
        self.stub.good()
        self.stub.forge = lambda data, request: signed_answer(data, request, token='y' * 40)
        with self.assertRaises(validator.ValidatorUnavailable):
            self.client.validate(self.xml, 'invoice')

    def test_a_captured_bearer_does_not_let_anyone_sign_an_answer(self):
        """A sniffer sees only the bearer. Signing with it (the obvious mistake) must be rejected."""
        self.stub.good()
        bearer = validator.auth_secret(VALIDATOR_TOKEN)
        self.stub.forge = lambda data, request: signed_answer(data, request, token=bearer)
        with self.assertRaises(validator.ValidatorUnavailable):
            self.client.validate(self.xml, 'invoice')

    def test_an_answer_about_another_document_is_unavailable_even_if_correctly_signed(self):
        self.stub.good()
        other = hashlib.sha256(b'another document').hexdigest()
        self.stub.forge = lambda data, request: signed_answer(data, request, xml_sha256=other)
        with self.assertRaises(validator.ValidatorUnavailable):
            self.client.validate(self.xml, 'invoice')

    def test_a_recorded_answer_cannot_be_replayed_for_a_later_request(self):
        """A genuine answer for the same document, but to an earlier request (another nonce)."""
        self.stub.good()
        self.stub.forge = lambda data, request: signed_answer(data, request, nonce='ab' * 16)
        with self.assertRaises(validator.ValidatorUnavailable):
            self.client.validate(self.xml, 'invoice')

    def test_tampering_with_the_failures_breaks_the_signature(self):
        self.stub.good([{'id': 'ibr-190-ae', 'flag': 'fatal', 'location': '', 'text': 'x'}])
        self.stub.forge = lambda data, request: dict(data, failures=[])  # strip the failure, keep the signature
        with self.assertRaises(validator.ValidatorUnavailable):
            self.client.validate(self.xml, 'invoice')

    def test_odd_but_well_formed_json_never_escapes_as_an_exception(self):
        """A non-ASCII signature made compare_digest raise TypeError; absurd nesting raises RecursionError."""
        self.stub.good()
        for forged in (
            lambda data, request: dict(data, signature='é' * 64),
            lambda data, request: dict(data, signature=12345),
            lambda data, request: dict(data, failures=[{'id': ['x']}]),
            lambda data, request: dict(data, failures={'a': 1}),
        ):
            self.stub.forge = forged
            with self.assertRaises(validator.ValidatorUnavailable):
                self.client.validate(self.xml, 'invoice')
        self.stub.forge = None
        self.stub.raw = b'[' * 100000 + b']' * 100000
        with self.assertRaises(validator.ValidatorUnavailable):
            self.client.validate(self.xml, 'invoice')

    def test_a_garbled_http_answer_is_unavailable_not_an_exception(self):
        # a response that is not HTTP at all makes http.client raise BadStatusLine, which is not an OSError
        import socket

        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        port = listener.getsockname()[1]

        def answer_garbage():
            connection, _addr = listener.accept()
            connection.recv(65536)
            connection.sendall(b'THIS IS NOT HTTP\r\n\r\n')
            connection.close()

        threading.Thread(target=answer_garbage, daemon=True).start()
        client = validator.HttpValidatorClient('http://127.0.0.1:%d' % port, timeout=3, token=VALIDATOR_TOKEN)
        try:
            with self.assertRaises(validator.ValidatorUnavailable):
                client.validate(self.xml, 'invoice')
        finally:
            listener.close()

    def test_redirects_are_never_followed(self):
        self.stub.redirect_to = 'http://127.0.0.1:9/elsewhere'
        with self.assertRaises(validator.ValidatorUnavailable):
            self.client.validate(self.xml, 'invoice')

    def test_an_oversized_document_is_not_sent(self):
        with self.assertRaises(validator.ValidatorUnavailable):
            self.client.validate(b'x' * (validator.MAX_BYTES + 1), 'invoice')
        self.assertEqual(self.stub.seen, [])

    def test_an_unknown_kind_is_a_programming_error(self):
        with self.assertRaises(ValueError):
            self.client.validate(self.xml, 'receipt')

    def test_an_oversized_answer_is_unavailable(self):
        self.stub.raw = b'x' * (validator.MAX_RESPONSE_BYTES + 10)
        with self.assertRaises(validator.ValidatorUnavailable):
            self.client.validate(self.xml, 'invoice')
