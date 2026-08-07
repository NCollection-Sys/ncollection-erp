# -*- coding: utf-8 -*-
"""Tests for the HTTP boundary to the gateway satellite (P5-T03 / #60).

Written because a reviewer flagged their absence twice, in two consecutive
rounds. Every other test in this module mocks `_complete` out entirely, so the
real error handling — four distinct branches, all of them user-facing — had
never executed under test.

`urlopen` is patched rather than a server being started: the point is the
mapping from transport failure to the message a person reads, and that needs no
socket.
"""
import io
import json
import os
import urllib.error

from unittest.mock import patch

from odoo.addons.ncollection_ai.models import gateway_client
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


class _FakeResponse(io.BytesIO):
    """urlopen's context-manager protocol, over a fixed body."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@tagged('post_install', '-at_install')
class TestGatewayClient(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.gateway = cls.env['ncollection.ai.gateway']

    def setUp(self):
        super().setUp()
        # Requests are signed per tenant (#373), so the master must be present
        # or _complete refuses before it reaches the transport. patch.dict
        # restores the real environment afterwards.
        patcher = patch.dict(
            os.environ, {gateway_client._GATEWAY_KEY_ENV: 'test-master-key'})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _with_urlopen(self, side_effect):
        return patch('urllib.request.urlopen', side_effect=side_effect)

    # ------------------------------------------------------------ happy path
    def test_a_successful_response_is_parsed_and_returned(self):
        payload = {'text': 'hello', 'usage': {'input_tokens': 10}}

        with self._with_urlopen(
                lambda *a, **k: _FakeResponse(json.dumps(payload).encode())):
            result = self.gateway._complete('a sanitised prompt')
        self.assertEqual(result, payload)

    def test_the_request_carries_this_database_as_the_tenant(self):
        """db-per-tenant means the cursor's database IS the tenant, so there is
        nothing for a caller to pass and therefore nothing to spoof from here.
        (Authenticating that claim AT THE GATEWAY is #373.)"""
        captured = {}

        def fake_urlopen(request, timeout=None):
            captured['body'] = json.loads(request.data.decode())
            return _FakeResponse(b'{"text": "ok"}')

        with patch('urllib.request.urlopen', new=fake_urlopen):
            self.gateway._complete('prompt', max_tokens=64)
        self.assertEqual(captured['body']['tenant'], self.env.cr.dbname)
        self.assertEqual(captured['body']['max_tokens'], 64)

    # ------------------------------------------------------------ signing
    def test_the_request_is_signed_for_this_database(self):
        """#373 — the gateway used to believe whatever tenant it was told.

        The signature is over the timestamp AND the exact body bytes, keyed by
        HMAC(master, 'nc-ai-gateway:' + dbname). Recomputing it here with the
        same inputs proves the wire format is what the satellite verifies —
        the two sides are separate code (the addon must not import
        satellites/), so this is the seam that can silently drift.
        """
        captured = {}

        def fake_urlopen(request, timeout=None):
            captured['headers'] = dict(request.headers)
            captured['body'] = request.data
            return _FakeResponse(b'{"text": "ok"}')

        with patch('urllib.request.urlopen', new=fake_urlopen):
            self.gateway._complete('prompt')

        headers = {k.lower(): v for k, v in captured['headers'].items()}
        stamp = headers['x-nc-timestamp']
        expected = gateway_client._sign(
            'test-master-key', self.env.cr.dbname, stamp, captured['body'])
        self.assertEqual(headers['x-nc-signature'], expected)

    def test_a_different_database_produces_a_different_signature(self):
        """The whole point of deriving PER TENANT: holding one workspace's key
        must not let you sign as another. If these ever matched, a single
        leaked key would be platform-wide."""
        body, stamp = b'{"tenant": "x"}', '1700000000'
        self.assertNotEqual(
            gateway_client._sign('m', 'acme', stamp, body),
            gateway_client._sign('m', 'globex', stamp, body))

    def test_a_missing_master_refuses_before_sending_anything(self):
        """Fail closed. An unsigned request would be rejected by the satellite
        anyway, but refusing here means the prompt never leaves the process."""
        sent = []

        def fake_urlopen(request, timeout=None):
            sent.append(request)
            return _FakeResponse(b'{}')

        with patch.dict(os.environ, {gateway_client._GATEWAY_KEY_ENV: ''}):
            with patch('urllib.request.urlopen', new=fake_urlopen):
                with self.assertRaises(UserError) as caught:
                    self.gateway._complete('prompt')
        self.assertIn(gateway_client._GATEWAY_KEY_ENV, str(caught.exception))
        self.assertFalse(sent, "nothing may be transmitted without a signature")

    # --------------------------------------------------------- error mapping
    def test_an_http_error_surfaces_the_gateways_own_message(self):
        """The satellite's error bodies are deliberately friendly and carry no
        tenant content (P5-T02), so a user over their allowance should read
        that, not a stack trace."""
        body = json.dumps({'message': 'Monthly AI budget exhausted.'}).encode()
        error = urllib.error.HTTPError(
            'http://x/v1/complete', 429, 'Too Many Requests', {},
            io.BytesIO(body))

        with self._with_urlopen(error):
            with self.assertRaises(UserError) as caught:
                self.gateway._complete('prompt')
        self.assertIn('Monthly AI budget exhausted.', str(caught.exception))

    def test_an_http_error_with_an_unreadable_body_falls_back_to_the_code(self):
        """A gateway that returns HTML or an empty body must not turn a 502 into
        a JSON parse traceback."""
        error = urllib.error.HTTPError(
            'http://x/v1/complete', 502, 'Bad Gateway', {},
            io.BytesIO(b'<html>nope</html>'))

        with self._with_urlopen(error):
            with self.assertRaises(UserError) as caught:
                self.gateway._complete('prompt')
        self.assertIn('502', str(caught.exception))

    def test_an_unreachable_satellite_says_how_to_start_it(self):
        """The overlay is opt-in, so 'not running' is a NORMAL state and the
        most likely one. Saying so beats leaving someone to read a socket
        error."""
        with self._with_urlopen(urllib.error.URLError('Connection refused')):
            with self.assertRaises(UserError) as caught:
                self.gateway._complete('prompt')
        message = str(caught.exception)
        self.assertIn('not reachable', message)
        self.assertIn('make ai-up', message)

    def test_a_malformed_response_body_becomes_a_user_error(self):
        with self._with_urlopen(lambda *a, **k: _FakeResponse(b'not json')):
            with self.assertRaises(UserError) as caught:
                self.gateway._complete('prompt')
        self.assertIn('unreadable', str(caught.exception))

    def test_a_socket_failure_becomes_a_user_error(self):
        with self._with_urlopen(OSError('connection reset')):
            with self.assertRaises(UserError) as caught:
                self.gateway._complete('prompt')
        self.assertIn('unreadable', str(caught.exception))

    # ---------------------------------------------------------------- bounds
    def test_the_response_read_is_capped(self):
        """A satellite that streams forever must not exhaust tenant memory.

        THIS TEST WAS VACUOUS AND A REVIEWER PROVED IT. The first version fed
        an unterminated JSON body and asserted UserError — which is raised
        whether or not the read is capped, since the body is unparseable at any
        length. Deleting the cap from the source did not fail it.

        So it now asserts on the CALL: the read must be bounded, and the bound
        must be the constant. That fails the moment someone writes read().
        """
        seen = {}

        # Deliberately NOT a subclass of _FakeResponse. Overriding read() there
        # trips pylint-odoo's method-required-super, and adding super() then
        # trips its missing-return — a standalone stub satisfies both by not
        # being an override at all.
        class _RecordingResponse:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            # pylint: disable=method-required-super
            # Not an override: this class has no parent, it is a stub
            # implementing urlopen's response protocol. pylint-odoo flags any
            # method named `read` regardless, and `read` is fixed by that
            # protocol. Subclassing io.BytesIO to satisfy the check instead
            # trips missing-return, so this is a disable rather than debt —
            # same treatment as the print-used disable in seed_tenant.py (#221).
            def read(self, size=-1):
                seen['size'] = size
                return b'{"text": "ok"}'

        with self._with_urlopen(lambda *a, **k: _RecordingResponse()):
            self.gateway._complete('prompt')
        self.assertEqual(seen.get('size'), gateway_client._MAX_RESPONSE_BYTES,
                         "the response body must be read with an explicit "
                         "byte cap, not read() in full")
