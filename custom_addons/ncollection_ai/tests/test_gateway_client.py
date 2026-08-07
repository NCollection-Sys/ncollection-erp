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
import urllib.error

from unittest.mock import patch

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

    def test_a_socket_failure_mid_read_becomes_a_user_error(self):
        with self._with_urlopen(OSError('connection reset')):
            with self.assertRaises(UserError) as caught:
                self.gateway._complete('prompt')
        self.assertIn('unreadable', str(caught.exception))

    # ---------------------------------------------------------------- bounds
    def test_an_oversized_response_is_not_read_without_limit(self):
        """A satellite that streams forever must not exhaust tenant memory.
        The read is capped, so an over-long body fails as unparseable rather
        than being swallowed whole."""
        with self._with_urlopen(
                lambda *a, **k: _FakeResponse(b'{"text": "' + b'x' * 2_000_000)):
            with self.assertRaises(UserError):
                self.gateway._complete('prompt')
