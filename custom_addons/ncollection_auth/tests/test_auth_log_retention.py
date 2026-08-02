# -*- coding: utf-8 -*-
"""Auth-log retention purge (#219).

The properties worth protecting are the ones that fail silently. A retention
policy that quietly stops running looks identical, from the outside, to one
that is working — nobody notices until an audit asks why there are two years of
IP addresses in a table that promised 180 days.

So these tests pin the three states the purge can be in: deleting the right
rows, keeping the right rows, and being deliberately switched off.

``create_date`` is an auto-set magic column, so rows are backdated with direct
SQL and the ORM cache invalidated afterwards. Writing it through the ORM is not
possible, and freezing the clock would test the mock rather than the query.
"""

from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.ncollection_auth.models import auth_log
from odoo.addons.ncollection_auth.models.auth_log import (
    DEFAULT_RETENTION_DAYS,
    DIGEST_PREFIX,
    MIN_RETENTION_DAYS,
    RETENTION_PARAM,
    SKELETON_PARAM,
)


@tagged("post_install", "-at_install")
class TestAuthLogRetention(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Log = cls.env["ncollection.auth.log"]
        cls.Param = cls.env["ir.config_parameter"].sudo()

    def _make_log(self, age_days, login="probe@ncollection.test"):
        """Create one row and backdate it by ``age_days``."""
        record = self.Log.sudo().create({
            "event_type": "login_success",
            "login": login,
            "ip_address": "203.0.113.7",
            "user_agent": "pytest/1.0",
        })
        if age_days:
            stamp = fields.Datetime.now() - timedelta(days=age_days)
            self.env.cr.execute(
                "UPDATE ncollection_auth_log SET create_date = %s WHERE id = %s",
                (stamp, record.id),
            )
            record.invalidate_recordset(["create_date"])
        return record

    # -- the window ---------------------------------------------------------

    def test_rows_past_the_window_are_deleted_and_recent_rows_kept(self):
        """The core contract of STAGE 2, both halves asserted in one place.

        Since #261 deletion is gated by SKELETON_PARAM, not RETENTION_PARAM —
        a row past retention is MINIMISED, and only a row past the skeleton
        window is removed. This drives the skeleton window directly.
        """
        self.Param.set_param(SKELETON_PARAM, 180)
        stale = self._make_log(age_days=400, login="stale@ncollection.test")
        edge_old = self._make_log(age_days=181, login="edge_old@ncollection.test")
        edge_new = self._make_log(age_days=179, login="edge_new@ncollection.test")
        fresh = self._make_log(age_days=0, login="fresh@ncollection.test")

        deleted = self.Log._gc_auth_log()

        self.assertEqual(deleted, 2, "exactly the two out-of-window rows go")
        self.assertFalse(stale.exists(), "a 400-day-old row must be purged")
        self.assertFalse(edge_old.exists(), "a 181-day-old row must be purged")
        self.assertTrue(edge_new.exists(), "a 179-day-old row must be kept")
        self.assertTrue(fresh.exists(), "today's row must be kept")

    def test_a_shorter_window_purges_more(self):
        """The window is actually read, not hardcoded at the default.

        Uses 60 days rather than something tighter because MIN_RETENTION_DAYS
        refuses anything under 30 — a row that would survive the 400-day
        default must still be purged under a legitimately shorter one.

        BOTH windows move together: since #261 the skeleton window may not be
        shorter than the retention window, so shrinking only the skeleton is a
        refused misconfiguration, not a shorter policy.
        """
        self.Param.set_param(RETENTION_PARAM, 30)
        self.Param.set_param(SKELETON_PARAM, 60)
        recent = self._make_log(age_days=90, login="ninety@ncollection.test")
        self.Log._gc_auth_log()
        self.assertFalse(
            recent.exists(),
            "a 90-day-old row must be purged under a 60-day window",
        )

    # -- the off switch -----------------------------------------------------

    def test_zero_disables_the_purge(self):
        """`<= 0` means disabled — the documented escape hatch."""
        self.Param.set_param(SKELETON_PARAM, 0)
        ancient = self._make_log(age_days=9999, login="ancient@ncollection.test")
        self.assertEqual(self.Log._gc_auth_log(), 0)
        self.assertTrue(ancient.exists(), "nothing may be deleted when disabled")

    def test_negative_disables_the_purge(self):
        self.Param.set_param(SKELETON_PARAM, -1)
        ancient = self._make_log(age_days=9999, login="neg@ncollection.test")
        self.assertEqual(self.Log._gc_auth_log(), 0)
        self.assertTrue(ancient.exists())

    # -- configuration robustness ------------------------------------------

    def test_missing_param_falls_back_to_the_default(self):
        """An unset parameter must not silently disable retention."""
        self.Param.search([("key", "=", RETENTION_PARAM)]).unlink()
        self.assertEqual(self.Log._retention_days(), DEFAULT_RETENTION_DAYS)

    def test_garbage_param_refuses_to_run_and_deletes_nothing(self):
        """An unparseable value must not be guessed at.

        Falling back to the 180-day default was the original behaviour and it
        was wrong for a DESTRUCTIVE job: an operator typing "3,650" for a
        ten-year mandate would have had it silently replaced by a much SHORTER
        window, and the purge would then delete exactly the data they meant to
        keep. Errors must not cause unintended state change.
        """
        self.Param.set_param(RETENTION_PARAM, "not-a-number")
        ancient = self._make_log(age_days=9999, login="garbage@ncollection.test")
        with self.assertRaises(UserError):
            self.Log._gc_auth_log()
        self.assertTrue(ancient.exists(), "nothing may be deleted on bad config")

    def test_float_string_is_refused_not_truncated(self):
        """"180.5" is not a whole number of days — refuse rather than guess."""
        self.Param.set_param(RETENTION_PARAM, "180.5")
        with self.assertRaises(UserError):
            self.Log._retention_days()

    # -- the floor ----------------------------------------------------------

    def test_window_below_the_floor_is_refused(self):
        """A tiny positive window is refused, not clamped (anti-forensics).

        Before this purge existed the ACL made the table append-only at the
        app layer (perm_unlink=0 for everyone, base.group_system included), so
        this cron is the FIRST application path that can delete audit rows.
        ir.config_parameter keeps no history, so setting the window to 1,
        letting it purge, and setting it back to 180 would erase evidence of an
        intrusion with no record the value ever changed. Clamping would still
        delete; refusing preserves the evidence.
        """
        self.Param.set_param(RETENTION_PARAM, 1)
        ancient = self._make_log(age_days=9999, login="floor@ncollection.test")
        with self.assertRaises(UserError):
            self.Log._gc_auth_log()
        self.assertTrue(ancient.exists(), "a 1-day window must delete nothing")

    def test_the_floor_itself_is_allowed(self):
        """The boundary is inclusive — MIN_RETENTION_DAYS is a valid setting."""
        self.Param.set_param(RETENTION_PARAM, MIN_RETENTION_DAYS)
        self.assertEqual(self.Log._retention_days(), MIN_RETENTION_DAYS)

    def test_zero_is_still_allowed_below_the_floor(self):
        """0 means "deliberately disabled" and must not trip the floor."""
        self.Param.set_param(RETENTION_PARAM, 0)
        self.assertEqual(self.Log._retention_days(), 0)

    def test_purge_is_idempotent(self):
        """A second run over an already-clean window is a no-op."""
        self.Param.set_param(SKELETON_PARAM, 180)
        self._make_log(age_days=400)
        first = self.Log._gc_auth_log()
        second = self.Log._gc_auth_log()
        self.assertEqual(first, 1)
        self.assertEqual(second, 0, "re-running must delete nothing further")

    # -- minimisation, stage 1 (#261) --------------------------------------

    def test_a_row_past_retention_is_minimised_NOT_deleted(self):
        """The whole behavioural change.

        #219 hard-deleted at 180 days, which destroyed the login_failed run-up
        and the login_success for a compromised session before the ~200-day
        mean time-to-identify had elapsed. Now that row survives with its
        identifying detail stripped.
        """
        self.Param.set_param(RETENTION_PARAM, 180)
        self.Param.set_param(SKELETON_PARAM, 400)
        row = self._make_log(age_days=200, login="target@ncollection.test")

        self.Log._minimise_auth_log()
        row.invalidate_recordset()

        self.assertTrue(row.exists(), "a row past retention must NOT be deleted")
        self.assertFalse(row.ip_address, "ip_address must be dropped")
        self.assertFalse(row.user_agent, "user_agent must be dropped")
        self.assertTrue(row.login.startswith(DIGEST_PREFIX),
                        "login must be digested, not left in the clear")
        self.assertNotIn("target@ncollection.test", row.login)
        # ...and the pattern evidence survives, which is the point.
        self.assertEqual(row.event_type, "login_success")
        self.assertTrue(row.create_date)

    def test_a_row_inside_retention_is_untouched(self):
        self.Param.set_param(RETENTION_PARAM, 180)
        row = self._make_log(age_days=10, login="recent@ncollection.test")
        self.Log._minimise_auth_log()
        row.invalidate_recordset()
        self.assertEqual(row.ip_address, "203.0.113.7")
        self.assertEqual(row.login, "recent@ncollection.test")

    def test_the_digest_is_stable_and_distinguishing(self):
        """Two failures against the same account must still be linkable, and
        two different accounts must not collide — otherwise the skeleton cannot
        answer 'was one account targeted repeatedly?', which is why `login` is
        digested rather than nulled."""
        self.Param.set_param(RETENTION_PARAM, 180)
        a1 = self._make_log(age_days=200, login="same@ncollection.test")
        a2 = self._make_log(age_days=201, login="same@ncollection.test")
        b1 = self._make_log(age_days=202, login="other@ncollection.test")

        self.Log._minimise_auth_log()
        for r in (a1, a2, b1):
            r.invalidate_recordset()

        self.assertEqual(a1.login, a2.login, "same account must digest alike")
        self.assertNotEqual(a1.login, b1.login, "different accounts must differ")

    def test_minimisation_is_idempotent(self):
        """Rule 12. The second run must be a genuine no-op, not a re-digest of
        an already-digested value — which would break the linkability above."""
        self.Param.set_param(RETENTION_PARAM, 180)
        row = self._make_log(age_days=200, login="idem@ncollection.test")

        first = self.Log._minimise_auth_log()
        row.invalidate_recordset()
        digest = row.login
        second = self.Log._minimise_auth_log()
        row.invalidate_recordset()

        self.assertEqual(first, 1)
        self.assertEqual(second, 0, "a second sweep must select nothing")
        self.assertEqual(row.login, digest, "the digest must not be re-digested")

    def test_a_row_with_no_login_minimises_without_error(self):
        """login is nullable — an event with no request context has none. The
        sweep must still strip ip/user_agent rather than skip or raise."""
        self.Param.set_param(RETENTION_PARAM, 180)
        row = self._make_log(age_days=200, login=False)
        self.Log._minimise_auth_log()
        row.invalidate_recordset()
        self.assertTrue(row.exists())
        self.assertFalse(row.ip_address)

    def test_zero_retention_disables_minimisation(self):
        self.Param.set_param(RETENTION_PARAM, 0)
        row = self._make_log(age_days=9999, login="off@ncollection.test")
        self.assertEqual(self.Log._minimise_auth_log(), 0)
        row.invalidate_recordset()
        self.assertEqual(row.login, "off@ncollection.test")

    # -- the two windows must stay coherent --------------------------------

    def test_a_skeleton_window_shorter_than_retention_is_refused(self):
        """Refused, not clamped — same reasoning as MIN_RETENTION_DAYS. A
        skeleton shorter than retention deletes rows before they are ever
        minimised, silently collapsing #261 back into #219's flat delete."""
        self.Param.set_param(RETENTION_PARAM, 180)
        self.Param.set_param(SKELETON_PARAM, 90)
        old_row = self._make_log(age_days=9999, login="coherent@ncollection.test")
        with self.assertRaises(UserError) as ctx:
            self.Log._gc_auth_log()
        self.assertIn("shorter than", str(ctx.exception))
        self.assertTrue(old_row.exists(), "nothing may be deleted when refusing")

    def test_a_garbage_skeleton_window_refuses_rather_than_guessing(self):
        self.Param.set_param(SKELETON_PARAM, "not-a-number")
        old_row = self._make_log(age_days=9999, login="garbage@ncollection.test")
        with self.assertRaises(UserError):
            self.Log._gc_auth_log()
        self.assertTrue(old_row.exists())

    def test_the_cron_runs_BOTH_stages(self):
        """A cron that only purged would delete un-minimised rows at 400 days
        having never stripped them at 180 — the PII would simply live longer
        than before, which is the opposite of the intent."""
        cron = self.env.ref("ncollection_auth.cron_gc_auth_log")
        self.assertIn("_minimise_auth_log", cron.code)
        self.assertIn("_gc_auth_log", cron.code)
        self.assertLess(cron.code.index("_minimise_auth_log"),
                        cron.code.index("_gc_auth_log"),
                        "minimise must run before purge")

    # -- the wiring ---------------------------------------------------------

    def test_cron_exists_and_points_at_the_purge(self):
        """The scheduled action is the deliverable, not just the method.

        A correct _gc_auth_log that nothing ever calls is a retention policy
        that never runs.
        """
        cron = self.env.ref("ncollection_auth.cron_gc_auth_log")
        self.assertTrue(cron.active, "the retention cron must ship enabled")
        self.assertEqual(cron.model_id.model, "ncollection.auth.log")
        self.assertIn("_gc_auth_log", cron.code)

    def test_create_date_is_indexed(self):
        """The purge filters on create_date; Odoo does not index it by default.

        Without the index this is a sequential scan, nightly, on a table that
        grows with every auth event on every tenant.

        Asserts the SPECIFIC index by name. An earlier version substring-matched
        "create_date" across every index definition joined together, which would
        also have passed on an unrelated index that merely mentioned the column.
        """
        self.env.cr.execute(
            "SELECT indexdef FROM pg_indexes "
            "WHERE tablename = %s AND indexname = %s",
            ("ncollection_auth_log", "ncollection_auth_log_create_date_index"),
        )
        row = self.env.cr.fetchone()
        self.assertTrue(
            row, "ncollection_auth_log_create_date_index does not exist")
        self.assertIn("create_date", row[0], "the index must cover create_date")

    # -- the batching loop --------------------------------------------------

    def test_multi_batch_purge_drains_the_whole_backlog(self):
        """The loop is the highest-risk logic here and had zero coverage.

        Patches the chunk size down rather than creating 5000 rows, so the
        multi-batch path, the oldest-first ordering and the short-batch early
        exit are all exercised cheaply.
        """
        self.patch(auth_log, "GC_CHUNK_SIZE", 3)
        self.Param.set_param(SKELETON_PARAM, 180)
        stale = [
            self._make_log(age_days=300 + n, login="batch%d@ncollection.test" % n)
            for n in range(7)
        ]
        fresh = self._make_log(age_days=0, login="batchfresh@ncollection.test")

        deleted = self.Log._gc_auth_log()

        self.assertEqual(deleted, 7, "all seven stale rows across three batches")
        for record in stale:
            self.assertFalse(record.exists())
        self.assertTrue(fresh.exists(), "the in-window row must survive")

    def test_per_run_cap_stops_and_leaves_the_rest(self):
        """Hitting GC_MAX_BATCHES stops the run without dropping the remainder.

        Without a cap a first run on a long-neglected tenant would sweep
        unbounded; the remainder must simply wait for tomorrow.
        """
        self.patch(auth_log, "GC_CHUNK_SIZE", 2)
        self.patch(auth_log, "GC_MAX_BATCHES", 2)
        self.Param.set_param(SKELETON_PARAM, 180)
        stale = [
            self._make_log(age_days=300 + n, login="cap%d@ncollection.test" % n)
            for n in range(6)
        ]

        deleted = self.Log._gc_auth_log()

        self.assertEqual(deleted, 4, "capped at 2 batches of 2")
        survivors = [record for record in stale if record.exists()]
        self.assertEqual(
            len(survivors), 2,
            "the rest must remain for the next run, not be silently skipped",
        )

    def test_oldest_rows_are_purged_first(self):
        """Ordering matters when the cap bites: drop the oldest PII first."""
        self.patch(auth_log, "GC_CHUNK_SIZE", 1)
        self.patch(auth_log, "GC_MAX_BATCHES", 1)
        self.Param.set_param(SKELETON_PARAM, 180)
        older = self._make_log(age_days=900, login="older@ncollection.test")
        newer = self._make_log(age_days=400, login="newer@ncollection.test")

        self.Log._gc_auth_log()

        self.assertFalse(older.exists(), "the oldest row must go first")
        self.assertTrue(newer.exists())
