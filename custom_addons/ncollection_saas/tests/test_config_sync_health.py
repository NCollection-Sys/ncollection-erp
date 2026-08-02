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

from unittest.mock import patch

import requests

from odoo.tests import TransactionCase, tagged


class _Response:
    """Minimal stand-in for a requests response."""

    def __init__(self, status_code, text=''):
        self.status_code = status_code
        self.text = text


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

    def test_recording_failure_never_breaks_a_FAILED_push_either(self):
        """Same guard on the path that matters most — a real failure."""
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
