# -*- coding: utf-8 -*-
"""Config-sync failure visibility (#264).

The properties worth protecting are the ones that fail quietly:

1. **A failure must not vanish.** Config sync propagates action_suspend into the
   tenant's workspace config, which P1-T10 reads. A push that fails and leaves
   no durable trace means a suspended subscription silently fails to lock the
   workspace — the customer keeps working and nobody can see why.
2. **Permanent must be distinguishable from transient.** A 401 is a stale key
   the nightly reconcile will retry forever without fixing; a timeout is exactly
   what that reconcile heals. Reporting them identically is what made "will fix
   itself" indistinguishable from "needs a human".
3. **The alert must fire once, not nightly.** The reconcile touches every ready
   tenant, so alerting per attempt trains everyone to ignore the channel.
4. **None of this may break a push.** `_config_sync_push` promises never to
   raise into a lifecycle transaction; observability that can break a suspension
   is worse than no observability.
"""

import gzip
import io
import json
from unittest.mock import MagicMock, patch

import requests
from urllib3.response import HTTPResponse

from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger

from odoo.addons.ncollection_saas.models.config_sync import (
    _MAX_RESPONSE_BYTES,
    _MAX_UNREPORTED_SYNCS,
    _READ_CHUNK,
    _RPC_DEADLINE,
)


class _Response:
    """Minimal stand-in for a requests response.

    Carries a json() body because the platform reads the tenant's
    required-job self-report off the sync response (#262). A stub without it
    exercised only the unreadable-body path.
    """

    def __init__(self, status_code, text='', body=None):
        self.status_code = status_code
        self.text = text
        self._body = {'ok': True} if body is None else body

    def json(self):
        if self._body is _UNREADABLE:
            raise ValueError('not json')
        return self._body


_UNREADABLE = object()


class _CountingRaw:
    """A urllib3-like .raw that records how many bytes were asked for (#278).

    The point of the cap is that the platform never READS an oversized body —
    not that it truncates one after loading it. Recording the requested size is
    what tells those two apart; asserting on the parsed result alone cannot.
    """

    def __init__(self, payload_bytes, raises=None):
        self._data = payload_bytes
        self._raises = raises
        self._pos = 0
        self.requested = None
        self.calls = 0
        self.returned = 0

    # pylint: disable=method-required-super
    # Not an Odoo model: this mimics urllib3's HTTPResponse.raw, whose API is
    # read(). There is no base class to call up into.
    def read(self, amt=None, decode_content=True):
        # ADVANCES, like a real stream. The earlier version re-served the same
        # slice on every call, which was harmless while the platform read once
        # but makes any chunked reader loop forever -- it hung a RED proof for
        # ten minutes before being spotted (#283).
        self.calls += 1
        self.requested = amt
        if self._raises is not None:
            raise self._raises
        end = len(self._data) if amt is None else self._pos + amt
        out = self._data[self._pos:end]
        self._pos = self._pos + len(out)
        self.returned += len(out)
        return out


class _StreamedResponse:
    """A stand-in that behaves like requests.Response(stream=True)."""

    def __init__(self, status_code, payload_bytes, text='', raises=None):
        self.status_code = status_code
        self._text = text
        self.raw = _CountingRaw(payload_bytes, raises=raises)
        self.closed = False
        # RECORDED, not raised. Raising from json()/text looks like a stronger
        # assertion but is a weaker one: _bounded_payload's fallback catches
        # bare Exception, so an AssertionError raised here is swallowed and the
        # test passes even though the fallback DID happen. Proven by breaking
        # the sentinel and watching this test stay green. Record the access and
        # assert on the flag instead.
        self.json_called = False
        self.text_read = False

    @property
    def text(self):
        self.text_read = True
        return self._text

    def json(self):
        self.json_called = True
        return {}

    def close(self):
        self.closed = True


class _RealGzipResponse:
    """A response whose .raw is a REAL urllib3 HTTPResponse, gzip-encoded.

    Every other double here hand-rolls .raw, so none of them exercise urllib3's
    actual decompression. That is the one property the cap depends on and the
    one nothing durable was checking: `raw.read(n, decode_content=True)` must
    bound the DECOMPRESSED output, not the compressed read. If it bounded only
    the compressed read, 64 KiB of gzip would inflate ~1000:1 and the cap would
    be decorative. Measured five ways during review; this makes it a test, so a
    urllib3 upgrade or a swapped HTTP client cannot silently take it away.
    """

    def __init__(self, status_code, payload_bytes):
        self.compressed = gzip.compress(payload_bytes)
        self.status_code = status_code
        self.text = ''
        self.raw = HTTPResponse(
            body=io.BytesIO(self.compressed),
            headers={'content-encoding': 'gzip'},
            status=status_code, preload_content=False)
        self.closed = False

    def json(self):
        raise AssertionError(
            "json() must not be called on a streamed response")

    def close(self):
        self.closed = True


@tagged("post_install", "-at_install")
class TestConfigSyncHealth(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tenant = cls.env['ncollection.tenant'].create({
            'company_name': 'Sync Health Co',
            'database_name': 'synchealth',
            'database_status': 'ready',
        })

    def _push(self, response=None, exception=None):
        """Run one push with the HTTP layer stubbed, and the key present."""
        with patch.dict('os.environ', {'NC_CONFIG_SYNC_KEY': 'test-master'}):
            if exception is not None:
                with patch('requests.post', side_effect=exception):
                    return self.tenant._config_sync_push('synchealth', {})
            with patch('requests.post', return_value=response):
                return self.tenant._config_sync_push('synchealth', {})

    # -- classification -----------------------------------------------------

    def test_auth_rejection_is_permanent(self):
        """401 means a stale bearer — retries cannot fix it."""
        self.assertFalse(self._push(_Response(401, 'unauthorized')))
        self.assertEqual(self.tenant.config_sync_state, 'permanent')
        self.assertIn('401', self.tenant.config_sync_last_error)

    def test_forbidden_is_permanent(self):
        self._push(_Response(403, 'forbidden'))
        self.assertEqual(self.tenant.config_sync_state, 'permanent')

    def test_server_error_is_transient(self):
        """A 500 is the reconcile's job, not a human's."""
        self._push(_Response(500, 'boom'))
        self.assertEqual(self.tenant.config_sync_state, 'transient')

    def test_transport_error_is_transient(self):
        self._push(exception=requests.ConnectionError('refused'))
        self.assertEqual(self.tenant.config_sync_state, 'transient')
        self.assertIn('transport', self.tenant.config_sync_last_error)

    def test_missing_master_key_is_permanent(self):
        """No key configured is a deployment fault; it will never self-heal."""
        with patch.dict('os.environ', {}, clear=True):
            self.assertFalse(self.tenant._config_sync_push('synchealth', {}))
        self.assertEqual(self.tenant.config_sync_state, 'permanent')

    # -- success and recovery ----------------------------------------------

    def test_success_records_a_timestamp_and_clears_the_error(self):
        self._push(_Response(500, 'boom'))
        self.assertEqual(self.tenant.config_sync_failure_count, 1)

        self._push(_Response(200))
        self.assertEqual(self.tenant.config_sync_state, 'ok')
        self.assertTrue(self.tenant.config_sync_last_ok,
                        "a successful push must stamp last_ok")
        self.assertFalse(self.tenant.config_sync_last_error)
        self.assertEqual(self.tenant.config_sync_failure_count, 0)

    def test_consecutive_failures_accumulate(self):
        """'Failing for three days' has to be answerable from the record."""
        for _ in range(3):
            self._push(_Response(500))
        self.assertEqual(self.tenant.config_sync_failure_count, 3)

    def test_recovery_is_announced(self):
        self._push(_Response(500))
        before = len(self.tenant.message_ids)
        self._push(_Response(200))
        self.assertGreater(
            len(self.tenant.message_ids), before,
            "recovery should be visible, not just an absence of alerts")

    def test_a_healthy_push_stays_quiet(self):
        """No chatter noise when nothing is wrong."""
        before = len(self.tenant.message_ids)
        self._push(_Response(200))
        self.assertEqual(len(self.tenant.message_ids), before)

    # -- alerting cadence ---------------------------------------------------

    def test_permanent_failure_opens_exactly_one_activity(self):
        """The nightly reconcile must not stack a to-do per attempt.

        Note this passes via the OUTER transition gate, not the dedup guard:
        calls 2-4 never reach _config_sync_alert at all. The dedup guard is
        covered by test_a_new_incident_after_recovery_opens_a_fresh_todo below.
        """
        for _ in range(4):
            self._push(_Response(401))
        activities = self.tenant.activity_ids.filtered(
            lambda a: a.summary and 'config sync' in a.summary.lower())
        self.assertEqual(
            len(activities), 1,
            "four failed pushes must leave one to-do, not four")

    def test_transient_failure_does_not_open_an_activity(self):
        """Something the reconcile heals is not a human task."""
        self._push(_Response(500))
        activities = self.tenant.activity_ids.filtered(
            lambda a: a.summary and 'config sync' in a.summary.lower())
        self.assertFalse(activities)

    def test_escalation_from_transient_to_permanent_alerts(self):
        """A retry that turns out to be unfixable must not be swallowed.

        Only the first failure of a run posts, so without an explicit
        escalation path a transient-then-permanent sequence would stay silent
        about the part that actually needs a human.
        """
        self._push(_Response(500))
        before = len(self.tenant.message_ids)
        self._push(_Response(401))
        self.assertEqual(self.tenant.config_sync_state, 'permanent')
        self.assertGreater(len(self.tenant.message_ids), before)

    def test_recovery_closes_the_open_todo(self):
        """A healed tenant must not leave an unresolved to-do behind."""
        self._push(_Response(401))
        self.assertTrue(self.tenant.config_sync_activity_id,
                        "a permanent failure should open a to-do")
        self._push(_Response(200))
        self.assertFalse(
            self.tenant.config_sync_activity_id,
            "recovery must resolve the to-do, not leave it open forever")

    def test_a_new_incident_after_recovery_opens_a_fresh_todo(self):
        """The bug this ticket would otherwise have reintroduced.

        permanent -> ok -> permanent. If recovery leaves the first to-do open,
        the dedup guard treats the SECOND, genuinely new incident as already
        reported: no fresh to-do, only a chatter line that is easy to miss.
        The only actionable channel would silently swallow it — exactly the
        failure mode #264 exists to prevent, one layer up.
        """
        self._push(_Response(401))
        first = self.tenant.config_sync_activity_id
        self.assertTrue(first)

        self._push(_Response(200))          # recovered
        self._push(_Response(401))          # a new, unrelated incident

        second = self.tenant.config_sync_activity_id
        self.assertTrue(
            second, "a new incident after recovery must open its own to-do")
        self.assertNotEqual(
            second, first,
            "the second incident must not be represented by the stale to-do")

    def test_dedup_does_not_depend_on_the_summary_text(self):
        """The guard keys on an explicit link, not a translated string.

        An earlier version matched 'config sync' inside the activity summary —
        which is passed through env._(), so on a non-English backoffice (an
        Arabic one is entirely plausible for a GCC product) the match would
        stop working and duplicates would stack.
        """
        self._push(_Response(401))
        activity = self.tenant.config_sync_activity_id
        self.assertTrue(activity)
        activity.summary = "totally unrelated wording"

        self._push(_Response(401))
        self.assertEqual(
            self.tenant.config_sync_activity_id, activity,
            "dedup must still hold when the summary text changes")

    def test_attribution_mismatch_is_refused(self):
        """A push whose target db does not match the record it would record on.

        Every call site passes them matched today, so this guards an invariant
        rather than a live bug — but recording an outcome on the wrong tenant
        would post chatter and open a to-do against an innocent customer.
        """
        with patch.dict('os.environ', {'NC_CONFIG_SYNC_KEY': 'k'}):
            with patch('requests.post', return_value=_Response(200)) as post:
                result = self.tenant._config_sync_push('someone-else', {})
        self.assertFalse(result)
        post.assert_not_called()
        self.assertEqual(self.tenant.config_sync_state, 'ok',
                         "no state should be recorded for a refused push")

    # -- the contract that must not break ----------------------------------

    @mute_logger('odoo.addons.ncollection_saas.models.config_sync')
    def test_recording_failure_never_breaks_the_push(self):
        """Observability that can break a suspension is worse than none.

        Breaks something INSIDE the recorder (the write), which is what its
        try/except actually guards. An earlier version of this test replaced
        the whole `_config_sync_record` method with a raising mock — that
        exercised a path the guard cannot cover and could never occur, so it
        failed while the production code was correct.
        """
        original_write = type(self.tenant).write

        def _explode(self, vals):
            if 'config_sync_state' in vals:
                raise RuntimeError('bookkeeping exploded')
            return original_write(self, vals)

        with patch.object(type(self.tenant), 'write', _explode):
            result = self._push(_Response(200))
        self.assertTrue(result, "a recording fault must not fail the push")

    @mute_logger('odoo.addons.ncollection_saas.models.config_sync')
    def test_recording_failure_never_breaks_a_FAILED_push_either(self):
        """Same guard on the path that matters most — a real failure.

        muted: the guard logs the traceback on purpose (a real bookkeeping
        failure needs a stack trace), but CI has a separate step that fails the
        build on any traceback in the log — "Odoo can exit 0 on some test
        failures". Deliberate fault injection must not look like a real crash.
        """
        original_write = type(self.tenant).write

        def _explode(self, vals):
            if 'config_sync_state' in vals:
                raise RuntimeError('bookkeeping exploded')
            return original_write(self, vals)

        with patch.object(type(self.tenant), 'write', _explode):
            result = self._push(_Response(401))
        self.assertFalse(result, "push still reports failure, without raising")

    def test_push_still_returns_false_on_failure(self):
        """The existing boolean contract is unchanged by the new bookkeeping."""
        self.assertFalse(self._push(_Response(500)))
        self.assertTrue(self._push(_Response(200)))

    # -- bounded response read (#278) --------------------------------------

    def test_the_platform_never_reads_more_than_the_cap(self):
        """The cap must stop the READ, not truncate afterwards.

        _RPC_TIMEOUT bounded how long we waited; nothing bounded how much we
        read, and resp.json() on the SUCCESS path materialised the whole body.
        A tenant could therefore size the control plane's allocation.
        """
        oversized = b'{"required_crons": {}}' + b'x' * (2 * _MAX_RESPONSE_BYTES)
        resp = _StreamedResponse(200, oversized)

        self.assertTrue(self._push(resp), "an oversized body must not fail the push")

        self.assertIsNotNone(resp.raw.requested, "the body was never read bounded")
        self.assertLessEqual(
            resp.raw.requested, _MAX_RESPONSE_BYTES + 1,
            "read more than the cap — the tenant sized the platform's read")
        # Per-call size alone is NOT the property. A loop that reads 4 KiB at a
        # time with no cumulative budget satisfies every per-call assertion
        # while materialising the whole body — demonstrated by review against
        # this very test. The budget is TOTAL bytes, so assert the total.
        # The read is chunked now (#283 needs a deadline check between
        # chunks), so "exactly one call" is no longer the property -- the
        # property was always the CUMULATIVE budget. Assert that, plus a call
        # ceiling, so a runaway loop is still caught.
        self.assertLessEqual(
            resp.raw.returned, _MAX_RESPONSE_BYTES + 1,
            "cumulative bytes read exceeded the cap")
        self.assertLessEqual(
            resp.raw.calls,
            (_MAX_RESPONSE_BYTES // _READ_CHUNK) + 3,
            "far more reads than the cap can justify — a runaway loop")
        self.assertTrue(resp.closed, "stream=True leaks the connection unless closed")

    def test_an_oversized_body_keeps_the_push_successful(self):
        """The config WAS applied. Marking this failed would misreport a
        suspension that actually landed — the no-raise contract #262 defends."""
        resp = _StreamedResponse(200, b'y' * (2 * _MAX_RESPONSE_BYTES))
        with mute_logger('odoo.addons.ncollection_saas.models.config_sync'):
            self.assertTrue(self._push(resp))
        self.tenant.invalidate_recordset()
        self.assertEqual(self.tenant.config_sync_state, 'ok')

    def test_an_oversized_body_cannot_HIDE_a_degraded_job(self):
        """Padding the response must not silently preserve a stale 'ok'.

        The cap makes the self-report unreadable, so a tenant could try to
        suppress its own compliance-job health by always answering oversized.
        _record_cron_health treats an absent report as 'unknown' rather than
        healthy, so the suppression is VISIBLE. That is the property that makes
        skipping the report an acceptable response to an oversized body.

        (Against a genuinely hostile tenant the self-report was never an
        attestation anyway — it could simply lie. This is about the signal not
        being silently frozen at its last good value.)
        """
        healthy = json.dumps({'required_crons': {
            'ncollection_auth.cron_gc_auth_log': {
                'installed': True, 'active': True}}}).encode()
        self.assertTrue(self._push(_StreamedResponse(200, healthy)))
        self.tenant.invalidate_recordset()
        self.assertEqual(
            self.tenant.cron_health_state, 'ok',
            "precondition: the signal must actually read 'ok' first, or the "
            "assertion below cannot tell 'reset to unknown' from 'left stale'")

        resp = _StreamedResponse(200, b'p' * (2 * _MAX_RESPONSE_BYTES))
        with mute_logger('odoo.addons.ncollection_saas.models.config_sync'):
            self.assertTrue(self._push(resp))
        self.tenant.invalidate_recordset()
        self.assertNotEqual(
            self.tenant.cron_health_state, 'ok',
            "an oversized body must not leave the health signal reading 'ok'")

    def test_a_normal_body_is_still_parsed_and_reported(self):
        """The cap must not break the #262 required-job self-report."""
        body = json.dumps({'required_crons': {
            'ncollection_auth.cron_gc_auth_log': {
                'installed': True, 'active': False}}}).encode()
        resp = _StreamedResponse(200, body)

        self.assertTrue(self._push(resp))

        self.tenant.invalidate_recordset()
        self.assertEqual(self.tenant.cron_health_state, 'degraded',
                         "a streamed body must still feed the health report")

    def test_an_error_body_excerpt_is_also_bounded(self):
        """The old code sliced resp.text[:500] — which truncates the LOG LINE
        after the whole body is in memory, and under stream=True would drain
        the entire stream to build that string."""
        resp = _StreamedResponse(500, b'z' * (2 * _MAX_RESPONSE_BYTES))
        with mute_logger('odoo.addons.ncollection_saas.models.config_sync'):
            self.assertFalse(self._push(resp))
        self.assertLessEqual(resp.raw.requested, _MAX_RESPONSE_BYTES + 1)
        self.assertTrue(resp.closed)

    def test_the_cap_boundary_itself(self):
        """The +1 read is the entire mechanism; test it AT the boundary.

        Exactly `cap` bytes must parse normally. One byte more must be
        discarded. Testing only at 2x cap never exercises the comparison.
        """
        body = {'required_crons': {
            'ncollection_auth.cron_gc_auth_log': {
                'installed': True, 'active': True}}}
        exact = json.dumps(body).encode()
        exact += b' ' * (_MAX_RESPONSE_BYTES - len(exact))
        self.assertEqual(len(exact), _MAX_RESPONSE_BYTES)

        self.assertTrue(self._push(_StreamedResponse(200, exact)))
        self.tenant.invalidate_recordset()
        self.assertEqual(self.tenant.cron_health_state, 'ok',
                         "a body of exactly the cap must still be parsed")

        with mute_logger('odoo.addons.ncollection_saas.models.config_sync'):
            self.assertTrue(self._push(
                _StreamedResponse(200, exact + b' ')))
        self.tenant.invalidate_recordset()
        self.assertNotEqual(self.tenant.cron_health_state, 'ok',
                            "one byte past the cap must be discarded")

    def test_a_failed_bounded_read_never_falls_back_to_the_whole_body(self):
        """The hole review found: a lying Content-Encoding is enough.

        A tenant answering `Content-Encoding: gzip` with a body that is not
        gzip makes urllib3 raise on the first bounded read. If that collapses
        to the same answer as "this response has no .raw", both callers reach
        for .json()/.text -- the unbounded pre-#278 path, re-opened for free.

        _StreamedResponse.json() raises AssertionError on purpose, so a
        fallback here fails the test loudly rather than passing quietly.
        """
        boom = _StreamedResponse(
            200, b'x' * 4096, text='THE-WHOLE-BODY',
            raises=ValueError('DecodeError: not gzipped'))
        with mute_logger('odoo.addons.ncollection_saas.models.config_sync'):
            self.assertTrue(self._push(boom), "must not break the push")
        self.assertEqual(boom.raw.calls, 1)
        self.assertEqual(
            boom.raw.returned, 0,
            "nothing was read bounded, so nothing may have been read at all")
        self.assertFalse(
            boom.json_called,
            "fell back to resp.json() after a FAILED bounded read — that is "
            "the unbounded pre-#278 path, reachable with one lying header")
        self.assertFalse(
            boom.text_read,
            "fell back to resp.text after a FAILED bounded read")

    def test_a_failed_read_on_the_error_path_does_not_read_text(self):
        """Same hole, error path: resp.text would drain the whole stream."""
        boom = _StreamedResponse(
            500, b'x' * 4096, text='THE-WHOLE-BODY',
            raises=ValueError('DecodeError: not gzipped'))
        with self.assertLogs(
                'odoo.addons.ncollection_saas.models.config_sync',
                level='ERROR') as caught:
            self.assertFalse(self._push(boom))
        self.assertNotIn(
            'THE-WHOLE-BODY', '\n'.join(caught.output),
            "the excerpt fell back to .text, reading the body unbounded")
        self.assertFalse(
            boom.text_read,
            "resp.text was touched at all — under stream=True that drains "
            "the entire body, which is exactly what the cap prevents")

    def test_a_mock_style_double_still_logs_the_real_body(self):
        """MagicMock auto-vivifies .raw, so a plain MagicMock double took the
        streamed branch and logged a MagicMock repr where the failure body
        belongs. test_config_sync.py uses exactly this idiom throughout, and
        its tests stayed green while the diagnostic line turned to garbage."""
        resp = MagicMock(status_code=403, text='denied')
        with self.assertLogs(
                'odoo.addons.ncollection_saas.models.config_sync',
                level='ERROR') as caught:
            self.assertFalse(self._push(resp))
        joined = '\n'.join(caught.output)
        self.assertIn('denied', joined,
                      "the failure body must still reach the log line")
        self.assertNotIn('MagicMock', joined,
                         "a mock repr leaked into the operator-facing log")

    def test_a_real_gzip_body_is_decoded_and_still_parsed(self):
        """Sanity half: the bounded read really does decompress.

        Without this, the next test could pass simply because the platform
        read raw gzip framing bytes and failed to parse them -- which would
        look identical to a working cap.
        """
        body = json.dumps({'required_crons': {
            'ncollection_auth.cron_gc_auth_log': {
                'installed': True, 'active': False}}}).encode()
        resp = _RealGzipResponse(200, body)
        self.assertLess(len(resp.compressed), len(body) + 64)

        self.assertTrue(self._push(resp))
        self.tenant.invalidate_recordset()
        self.assertEqual(
            self.tenant.cron_health_state, 'degraded',
            "a gzip'd body must decode and still feed the health report")

    def test_a_real_gzip_bomb_is_bounded_by_the_cap(self):
        """The cap must bound the DECOMPRESSED output, through real urllib3.

        8 MiB of decompressed padding from a few KiB on the wire. If the cap
        bounded only the compressed read, this body would sail under it and
        parse fine; the discard is the proof that the limit is applied after
        decoding.
        """
        payload = (b'{"required_crons": {}, "pad": "'
                   + b'A' * (8 * 1024 * 1024) + b'"}')
        resp = _RealGzipResponse(200, payload)
        self.assertLess(
            len(resp.compressed), _MAX_RESPONSE_BYTES,
            "precondition: the body must fit under the cap COMPRESSED, or "
            "this proves nothing about where the cap is applied")

        with mute_logger('odoo.addons.ncollection_saas.models.config_sync'):
            self.assertTrue(self._push(resp), "a bomb must not break the push")

        self.tenant.invalidate_recordset()
        self.assertEqual(self.tenant.cron_health_state, 'unknown')
        self.assertIn('discarded', self.tenant.cron_health_detail or '')

    def test_a_forged_log_line_is_neutralised(self):
        """CWE-117. This excerpt lands in the ERROR stream P2-T10 parses, so an
        embedded newline lets a tenant forge whole log records inside ours."""
        forged = (b'oops\n2026-01-01 00:00:00 999 ERROR nc odoo: '
                  b'FAKE ALERT injected by the tenant')
        resp = _StreamedResponse(500, forged)
        with self.assertLogs(
                'odoo.addons.ncollection_saas.models.config_sync',
                level='ERROR') as caught:
            self.assertFalse(self._push(resp))
        joined = '\n'.join(caught.output)
        self.assertIn('FAKE ALERT', joined, "the excerpt should still be shown")
        self.assertNotIn('oops\n', joined,
                         "a raw newline survived into the log stream")

    # -- wall-clock deadline (#283) ----------------------------------------

    def _fake_clock(self, *ticks):
        """A monotonic stand-in returning `ticks` in order, last value sticky.

        A test that really slept would add _RPC_DEADLINE seconds to CI, and
        patching time.monotonic globally would move Odoo's own clocks mid-test.
        """
        seq = list(ticks)

        def clock():
            return seq.pop(0) if len(seq) > 1 else seq[0]
        return clock

    def test_a_trickled_body_is_abandoned_at_the_deadline(self):
        """The byte cap cannot stop a trickle -- it never REACHES the cap.

        requests' timeout= is the connect and per-idle-gap bound, not a total
        duration: every byte that arrives resets it. Measured at 9.03s against
        a 2.0s timeout. Only a wall-clock deadline stops this.
        """
        # Small chunks, so the body would take many reads to finish.
        resp = _StreamedResponse(200, b'x' * (4 * _READ_CHUNK))
        clock = self._fake_clock(0.0, 1.0, _RPC_DEADLINE + 1.0)

        with patch.object(type(self.tenant), '_read_clock',
                          staticmethod(clock)), \
                mute_logger('odoo.addons.ncollection_saas.models.config_sync'):
            self.assertTrue(self._push(resp), "must not break the push")

        self.assertLess(
            resp.raw.returned, len(resp.raw._data),
            "the whole body was read despite the deadline elapsing")
        # "stopped somewhere before the end" is too weak: a loop checking the
        # clock every OTHER iteration reads twice as much and still passes it.
        # The claimed property is a check between EVERY chunk, so bound the
        # reads -- the fake clock elapses on its 3rd reading, permitting at
        # most two chunk reads.
        self.assertLessEqual(
            resp.raw.calls, 2,
            "more chunk reads than the deadline schedule allows — the clock "
            "is being checked less often than every chunk")
        self.assertTrue(resp.closed, "the connection was not released")

    def test_the_deadline_does_not_fire_on_a_healthy_push(self):
        """A guard that fires on ordinary traffic is worse than none."""
        body = json.dumps({'required_crons': {
            'ncollection_auth.cron_gc_auth_log': {
                'installed': True, 'active': True}}}).encode()
        clock = self._fake_clock(0.0, 0.001, 0.002)
        with patch.object(type(self.tenant), '_read_clock',
                          staticmethod(clock)):
            self.assertTrue(self._push(_StreamedResponse(200, body)))
        self.tenant.invalidate_recordset()
        self.assertEqual(self.tenant.cron_health_state, 'ok')

    def test_an_abandoned_read_never_falls_back_to_the_whole_body(self):
        """Giving up on the deadline then calling .json()/.text would resume
        the very read we just abandoned."""
        resp = _StreamedResponse(500, b'x' * (4 * _READ_CHUNK),
                                 text='THE-WHOLE-BODY')
        clock = self._fake_clock(0.0, _RPC_DEADLINE + 1.0)
        with patch.object(type(self.tenant), '_read_clock',
                          staticmethod(clock)), \
                self.assertLogs(
                    'odoo.addons.ncollection_saas.models.config_sync',
                    level='ERROR') as caught:
            self.assertFalse(self._push(resp))
        joined = '\n'.join(caught.output)
        # assertLogs(ERROR) alone proves nothing here: the HTTP 500 path logs
        # an ERROR regardless. Name the deadline, or this test passes with the
        # deadline check deleted.
        self.assertIn('read deadline', joined,
                      "the deadline never fired, so this proves nothing")
        self.assertFalse(resp.text_read, "fell back to .text after giving up")
        self.assertNotIn('THE-WHOLE-BODY', joined)

    # -- the real wall-clock bound (#285) ------------------------------------

    class _Read1Reader:
        """A BufferedReader stand-in: read1 returns on the FIRST data."""

        def __init__(self, data, per_call=1):
            self._data = data
            self._pos = 0
            self.per_call = per_call
            self.calls = 0

        def read1(self, amt=None):
            self.calls += 1
            end = min(len(self._data), self._pos + min(self.per_call, amt or 1))
            out = self._data[self._pos:end]
            self._pos = end
            return out

    def _read1_response(self, status, data, per_call=1):
        resp = _StreamedResponse(status, data)
        reader = self._Read1Reader(data, per_call=per_call)
        # Mirror the real shape the model walks: resp.raw._fp.fp
        resp.raw._fp = type('FP', (), {'fp': reader})()
        return resp, reader

    def test_the_deadline_is_checked_PER_RECV_not_per_chunk(self):
        """The whole point of #285.

        #283 checked the clock between 8 KiB chunks, and raw.read() blocks
        until it HAS 8 KiB — so against a trickle the check never ran. read1
        returns on the first available data, so a slow body is abandoned after
        a couple of recvs instead of never.
        """
        resp, reader = self._read1_response(200, b'x' * (4 * _READ_CHUNK))
        clock = self._fake_clock(0.0, 1.0, _RPC_DEADLINE + 1.0)

        with patch.object(type(self.tenant), '_read_clock',
                          staticmethod(clock)), \
                mute_logger('odoo.addons.ncollection_saas.models.config_sync'):
            self.assertTrue(self._push(resp), "must not break the push")

        # BOTH bounds, and the lower one matters most: with read1 ignored the
        # reader is never touched, so calls==0 and _pos==0 satisfied the
        # upper bound and the "didn't read it all" check alike. The test
        # could not tell "bounded via read1" from "read1 never used".
        self.assertGreaterEqual(
            reader.calls, 1,
            "the read1 reader was never used — this test would pass with the "
            "fast path disabled entirely")
        self.assertLessEqual(
            reader.calls, 2,
            "more recvs than the deadline schedule allows — the clock is not "
            "being checked per recv")
        self.assertLess(reader._pos, len(reader._data),
                        "read the whole body despite the deadline elapsing")

    def test_a_normal_body_still_arrives_through_read1(self):
        """The fast path must still deliver a real payload, or #262's
        required-job self-report silently stops working."""
        body = json.dumps({'required_crons': {
            'ncollection_auth.cron_gc_auth_log': {
                'installed': True, 'active': False}}}).encode()
        resp, reader = self._read1_response(200, body, per_call=7)

        self.assertTrue(self._push(resp))
        self.tenant.invalidate_recordset()
        self.assertEqual(self.tenant.cron_health_state, 'degraded',
                         "a body assembled from many read1 calls was lost")
        self.assertGreater(reader.calls, 1, "precondition: multiple recvs")

    def test_the_cumulative_cap_still_holds_on_the_read1_path(self):
        """#278's byte budget must survive the new reader."""
        resp, reader = self._read1_response(
            200, b'y' * (3 * _MAX_RESPONSE_BYTES), per_call=4096)
        with mute_logger('odoo.addons.ncollection_saas.models.config_sync'):
            self.assertTrue(self._push(resp))
        self.assertLessEqual(reader._pos, _MAX_RESPONSE_BYTES + 1,
                             "read past the cap on the read1 path")

    def test_the_fallback_fires_when_read1_is_unavailable(self):
        """A defensive branch nothing exercises is a branch that does not work.

        If a future urllib3 moves _fp/.fp, the model must fall back to chunked
        reads AND say so — a silent downgrade would leave the comment claiming
        a bound the code no longer keeps.
        """
        resp = _StreamedResponse(200, json.dumps({'required_crons': {}}).encode())
        self.assertIsNone(
            self.tenant._recv_reader(resp),
            "precondition: this double has no _fp.fp, so read1 is unavailable")

        with self.assertLogs(
                'odoo.addons.ncollection_saas.models.config_sync',
                level='WARNING') as caught:
            self.assertTrue(self._push(resp))
        self.assertIn('read1', '\n'.join(caught.output),
                      "the downgrade was silent")

    # -- escalation on repeated unreadable reports (#283) -------------------

    def _unreadable_push(self):
        with mute_logger('odoo.addons.ncollection_saas.models.config_sync'):
            return self._push(
                _StreamedResponse(200, b'p' * (2 * _MAX_RESPONSE_BYTES)))

    def _report_posts(self):
        return len(self.tenant.message_ids.filtered(
            lambda m: m.body and 'unreadable required-job' in m.body.lower()))

    def _report_todos(self):
        return self.tenant.activity_ids.filtered(
            lambda a: a.summary and 'unreadable' in a.summary.lower())

    def test_repeated_unreadable_reports_escalate_once(self):
        """'unknown' opens no to-do and is styled like a fresh tenant, so a
        tenant whose report is never readable was invisible forever -- exactly
        the blindness #262 exists to remove."""
        for _ in range(_MAX_UNREPORTED_SYNCS - 1):
            self._unreadable_push()
        self.tenant.invalidate_recordset()
        self.assertFalse(
            self._report_todos(),
            "escalated before the threshold — a one-off blip must stay quiet")

        self._unreadable_push()          # crosses
        self.tenant.invalidate_recordset()
        self.assertEqual(len(self._report_todos()), 1)

        posts = self._report_posts()
        self.assertEqual(posts, 1)

        for _ in range(3):               # the nightly reconcile keeps running
            self._unreadable_push()
        self.tenant.invalidate_recordset()
        self.assertEqual(len(self._report_todos()), 1)
        # The to-do count alone CANNOT detect re-alerting: _health_open_activity
        # already refuses to open a second one. Chatter is where nightly repeats
        # actually show up, and posting every night is what trains people to
        # ignore the channel. Proven: alerting at >= instead of == leaves the
        # to-do assertion green and only this one fails.
        self.assertEqual(
            self._report_posts(), posts,
            "re-posted after crossing; the alert must fire once, not nightly")

    def test_a_tenant_that_simply_never_reports_is_not_escalated(self):
        """The regression two reviewers reproduced independently.

        A missing report has two shapes. "We could not READ it" is #283's
        fault condition. "The body read fine and carries no required_crons" is
        a tenant provisioned before #262 — which this module's own header
        calls legitimate and says must never alert, because #218 is what
        backfills those.

        Counting both meant every pre-#262 tenant collected a miss nightly and
        got a false "unreadable" to-do on the third — self-inflicted alert
        fatigue, in the exact channel this ticket exists to keep credible.
        """
        for _ in range(_MAX_UNREPORTED_SYNCS + 2):
            self.tenant._record_cron_health(None, skip_reason=False)

        self.tenant.invalidate_recordset()
        self.assertEqual(
            self.tenant.cron_report_miss_count, 0,
            "a legitimate non-reporter was counted as a read failure")
        self.assertFalse(self.tenant.cron_report_activity_id)
        self.assertFalse(self._report_todos())
        self.assertEqual(self.tenant.cron_health_state, 'unknown',
                         "it is still 'not reported' — just not alertable")

    def test_a_null_miss_count_from_an_upgrade_does_not_crash(self):
        """A NULL column must not break a push.

        What this actually locks in is Odoo's ORM coercion: a NULL Integer is
        read as 0, so `+ 1` never sees None. Stated precisely because the first
        version of this test was written believing it proved the `or 0` guard
        in the model — it does not. Removing that guard leaves this test green,
        which is exactly how it was caught.
        """
        self.env.cr.execute(
            "UPDATE ncollection_tenant SET cron_report_miss_count = NULL "
            "WHERE id = %s", (self.tenant.id,))
        self.tenant.invalidate_recordset()

        self.assertTrue(self._unreadable_push(), "a NULL counter broke a push")
        self.tenant.invalidate_recordset()
        self.assertEqual(self.tenant.cron_report_miss_count, 1)

    def test_a_readable_report_clears_the_streak_and_the_todo(self):
        for _ in range(_MAX_UNREPORTED_SYNCS):
            self._unreadable_push()
        self.tenant.invalidate_recordset()
        self.assertEqual(len(self._report_todos()), 1)

        good = json.dumps({'required_crons': {
            'ncollection_auth.cron_gc_auth_log': {
                'installed': True, 'active': True}}}).encode()
        self.assertTrue(self._push(_StreamedResponse(200, good)))

        self.tenant.invalidate_recordset()
        self.assertEqual(self.tenant.cron_report_miss_count, 0)
        self.assertFalse(
            self._report_todos().filtered(lambda a: not a.date_done),
            "the to-do survived recovery — a stale to-do makes the NEXT "
            "genuine incident look already-reported")

    def test_a_degraded_report_still_clears_the_streak(self):
        """The streak counts 'could not READ', not 'jobs unhealthy'. A
        readable report saying a job is disabled is still a readable report."""
        for _ in range(_MAX_UNREPORTED_SYNCS):
            self._unreadable_push()
        bad = json.dumps({'required_crons': {
            'ncollection_auth.cron_gc_auth_log': {
                'installed': True, 'active': False}}}).encode()
        self.assertTrue(self._push(_StreamedResponse(200, bad)))
        self.tenant.invalidate_recordset()
        self.assertEqual(self.tenant.cron_report_miss_count, 0)
        self.assertEqual(self.tenant.cron_health_state, 'degraded')

    def test_the_unreadable_todo_is_not_the_disabled_job_todo(self):
        """Two different operator actions. Closing one must not resolve the
        other, which is why they have separate activity fields."""
        for _ in range(_MAX_UNREPORTED_SYNCS):
            self._unreadable_push()
        self.tenant.invalidate_recordset()
        self.assertTrue(self.tenant.cron_report_activity_id)
        self.assertNotEqual(self.tenant.cron_report_activity_id,
                            self.tenant.cron_health_activity_id)

    def test_a_non_streamed_response_still_works(self):
        """The fallback that keeps the existing contract. A response object
        without .raw — a non-streamed Response, or any of the doubles this
        suite already uses — must behave exactly as before."""
        self.assertTrue(self._push(_Response(200, body={'required_crons': {}})))
        self.tenant.invalidate_recordset()
        self.assertEqual(self.tenant.config_sync_state, 'ok')


@tagged("post_install", "-at_install")
class TestRequiredCronHealth(TransactionCase):
    """The platform's view of tenant-side jobs it depends on (#262).

    Odoo deactivates a cron by itself after 5 failures spanning 7 days, so a
    compliance job does not merely fail — it eventually switches OFF and goes
    quiet. A tenant module cannot see the fleet, so the tenant self-reports on
    the config-sync response and the platform watches.

    The distinction that matters most here: a job whose MODULE is not installed
    is not a fault. A tenant provisioned before that module existed legitimately
    lacks it (#218 backfills those), and alerting on it would be noise.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tenant = cls.env['ncollection.tenant'].create({
            'company_name': 'Cron Health Co',
            'database_name': 'cronhealth',
            'database_status': 'ready',
        })

    CRON = 'ncollection_auth.cron_gc_auth_log'

    def _report(self, installed=True, active=True):
        return {self.CRON: {'installed': installed, 'active': active}}

    # -- classification -----------------------------------------------------

    def test_enabled_job_is_healthy(self):
        self.tenant._record_cron_health(self._report())
        self.assertEqual(self.tenant.cron_health_state, 'ok')
        self.assertFalse(self.tenant.cron_health_detail)

    def test_disabled_job_is_degraded_and_named(self):
        self.tenant._record_cron_health(self._report(active=False))
        self.assertEqual(self.tenant.cron_health_state, 'degraded')
        self.assertIn(self.CRON, self.tenant.cron_health_detail)

    def test_absent_module_is_not_a_fault(self):
        """Pre-#218 tenants legitimately lack the module — do not cry wolf."""
        self.tenant._record_cron_health(self._report(installed=False, active=False))
        self.assertEqual(
            self.tenant.cron_health_state, 'ok',
            "a job whose module is not installed must not read as degraded")

    def test_missing_report_is_unknown_not_healthy(self):
        """Silence is not health. An older tenant reports nothing."""
        for absent in (None, {}, 'garbage', []):
            with self.subTest(payload=absent):
                self.tenant.cron_health_state = 'ok'
                self.tenant._record_cron_health(absent)
                self.assertEqual(self.tenant.cron_health_state, 'unknown')

    # -- alerting -----------------------------------------------------------

    def test_degradation_opens_exactly_one_todo(self):
        """The nightly reconcile must not stack a to-do per run.

        Note this passes via the OUTER transition gate — calls 2 and 3 never
        reach _alert_cron_health. The INNER dedup guard is covered by
        test_alert_is_idempotent_when_called_directly below.
        """
        for _ in range(3):
            self.tenant._record_cron_health(self._report(active=False))
        todos = self.tenant.activity_ids.filtered(
            lambda a: a.summary and 'required job' in a.summary.lower())
        self.assertEqual(len(todos), 1)

    def test_recovery_closes_the_todo(self):
        self.tenant._record_cron_health(self._report(active=False))
        self.assertTrue(self.tenant.cron_health_activity_id)
        self.tenant._record_cron_health(self._report())
        self.assertFalse(
            self.tenant.cron_health_activity_id,
            "recovery must resolve the to-do, or the next incident is swallowed")

    def test_a_new_degradation_after_recovery_opens_a_fresh_todo(self):
        """Same trap #264 hit: a stale to-do must not absorb a new incident."""
        self.tenant._record_cron_health(self._report(active=False))
        first = self.tenant.cron_health_activity_id
        self.tenant._record_cron_health(self._report())
        self.tenant._record_cron_health(self._report(active=False))
        second = self.tenant.cron_health_activity_id
        self.assertTrue(second)
        self.assertNotEqual(second, first)

    # -- the seam between the two halves ------------------------------------

    def test_health_is_read_off_a_real_push_response(self):
        """The tenant's report has to survive the whole round trip.

        Both halves are unit-tested separately, which is exactly how a seam
        goes untested: the tenant builds `required_crons`, the platform parses
        it off resp.json(). This drives a real _config_sync_push with a body
        shaped like the tenant's actual return value.
        """
        body = {'ok': True, 'plan_code': 'X', 'subscription_status': 'active',
                'required_crons': {self.CRON: {'installed': True,
                                               'active': False}}}
        with patch.dict('os.environ', {'NC_CONFIG_SYNC_KEY': 'k'}):
            with patch('requests.post',
                       return_value=_Response(200, body=body)):
                ok = self.tenant._config_sync_push('cronhealth', {})
        self.assertTrue(ok, "health reporting must not affect the push result")
        self.assertEqual(self.tenant.cron_health_state, 'degraded')

    def test_an_unreadable_body_does_not_fail_the_push(self):
        """A proxy returning HTML must not break a successful config push."""
        with patch.dict('os.environ', {'NC_CONFIG_SYNC_KEY': 'k'}):
            with patch('requests.post',
                       return_value=_Response(200, body=_UNREADABLE)):
                ok = self.tenant._config_sync_push('cronhealth', {})
        self.assertTrue(ok)
        self.assertEqual(self.tenant.cron_health_state, 'unknown')

    def test_alert_is_idempotent_when_called_directly(self):
        """Covers the INNER dedup guard, which the transition gate hides.

        Without this, deleting the guard inside _alert_cron_health would not
        fail a single test.
        """
        self.tenant._alert_cron_health('first')
        first = self.tenant.cron_health_activity_id
        self.tenant._alert_cron_health('second')
        self.assertEqual(
            self.tenant.cron_health_activity_id, first,
            "a second alert must not open a second to-do")

    def test_recovery_through_unknown_still_closes_the_todo(self):
        """degraded -> unknown -> ok. The path that left a to-do open forever.

        An unreadable response body flips the state to 'unknown', so on the
        eventual recovery `previous` is 'unknown', not 'degraded'. Closing only
        on degraded->ok therefore left the to-do open, and a stale to-do makes
        the NEXT real incident look already-reported — the exact trap #264's
        review caught, reappearing in this parallel implementation.
        """
        self.tenant._record_cron_health(self._report(active=False))
        self.assertTrue(self.tenant.cron_health_activity_id)

        self.tenant._record_cron_health(None)          # unreadable body
        self.assertEqual(self.tenant.cron_health_state, 'unknown')

        self.tenant._record_cron_health(self._report())  # healthy again
        self.assertEqual(self.tenant.cron_health_state, 'ok')
        self.assertFalse(
            self.tenant.cron_health_activity_id,
            "recovery must close the to-do regardless of the path taken")

    def test_a_probe_error_is_not_healthy(self):
        """The tenant could not answer — that is 'unknown', never 'ok'.

        The tenant ships an `error` flag when its own probe raises. It was
        previously computed, sent over the wire, and never consulted, so an
        errored probe resolved to 'healthy'.
        """
        self.tenant._record_cron_health(
            {self.CRON: {'installed': False, 'active': False, 'error': True}})
        self.assertEqual(self.tenant.cron_health_state, 'unknown')
        self.assertIn('could not read', self.tenant.cron_health_detail.lower())

    def test_a_malformed_job_name_is_ignored_not_rendered(self):
        """The report is TENANT-CONTROLLED input.

        A compromised tenant could stuff fabricated or oversized job names that
        would otherwise land in the platform's admin UI and chatter. Anything
        that is not a plausible xml-id is dropped.
        """
        self.tenant._record_cron_health({
            '<script>alert(1)</script>': {'installed': True, 'active': False},
            'x' * 500: {'installed': True, 'active': False},
        })
        self.assertEqual(
            self.tenant.cron_health_state, 'unknown',
            "nothing plausible was reported, so the state is unknown")
        self.assertNotIn('script', (self.tenant.cron_health_detail or '').lower())

    def test_an_absurd_number_of_entries_is_capped(self):
        """Bounded belief: a tenant cannot flood the platform record."""
        flood = {'mod%d.cron_x' % i: {'installed': True, 'active': False}
                 for i in range(500)}
        self.tenant._record_cron_health(flood)
        self.assertEqual(self.tenant.cron_health_state, 'degraded')
        self.assertLessEqual(len(self.tenant.cron_health_detail), 255)

    # -- the contract that must not break ----------------------------------

    @mute_logger('odoo.addons.ncollection_saas.models.config_sync')
    def test_recording_never_raises(self):
        """A health probe must never break a config push."""
        original_write = type(self.tenant).write

        def _explode(self, vals):
            if 'cron_health_state' in vals:
                raise RuntimeError('health bookkeeping exploded')
            return original_write(self, vals)

        with patch.object(type(self.tenant), 'write', _explode):
            self.tenant._record_cron_health(self._report(active=False))
        # No exception escaped; that is the assertion.
