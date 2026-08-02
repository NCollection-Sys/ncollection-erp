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

from unittest.mock import patch

from odoo import fields
from odoo.tools.safe_eval import safe_eval
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
        self.Param.set_param(RETENTION_PARAM, 30)   # distinct: pins that
        self.Param.set_param(SKELETON_PARAM, 180)  # gc reads SKELETON
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
        self.Param.set_param(RETENTION_PARAM, 30)   # distinct: pins that
        self.Param.set_param(SKELETON_PARAM, 180)  # gc reads SKELETON
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
        self.assertIn("flat delete wearing two stages", str(ctx.exception))
        self.assertTrue(old_row.exists(), "nothing may be deleted when refusing")

    def test_a_garbage_skeleton_window_refuses_rather_than_guessing(self):
        self.Param.set_param(SKELETON_PARAM, "not-a-number")
        old_row = self._make_log(age_days=9999, login="garbage@ncollection.test")
        with self.assertRaises(UserError):
            self.Log._gc_auth_log()
        self.assertTrue(old_row.exists())

    def test_the_cron_runs_BOTH_stages(self):
        """A cron that only purged would delete un-minimised rows at 400 days
        having never stripped them at 180 — the PII would live LONGER than
        before, the opposite of the intent.

        Executes the cron's own code and asserts on ROW STATE. The first version
        of this test only checked that the substrings appeared in `cron.code`, in
        order — which passes with stage 1 commented out, moved into a dead
        string, or wrapped in `if False:`. That is the ninth test in this repo
        found to pass against a broken implementation, so this one runs the
        thing.
        """
        self.Param.set_param(RETENTION_PARAM, 180)
        self.Param.set_param(SKELETON_PARAM, 400)
        to_minimise = self._make_log(age_days=200, login="mid@ncollection.test")
        to_delete = self._make_log(age_days=500, login="old@ncollection.test")
        untouched = self._make_log(age_days=10, login="new@ncollection.test")

        cron = self.env.ref("ncollection_auth.cron_gc_auth_log")
        # safe_eval the cron's real code with the same globals ir.cron provides,
        # so a broken `code` field fails HERE rather than silently in production.
        safe_eval(cron.code.strip(), {
            # No cron_id: it would make _commit_progress call cr.commit(),
            # which Odoo forbids inside a test (#219's own guard).
            'model': self.Log,
            'env': self.env,
            'log': lambda *a, **k: None,
        }, mode="exec")

        for row in (to_minimise, to_delete, untouched):
            row.invalidate_recordset()
        self.assertFalse(to_delete.exists(), "stage 2 did not delete")
        self.assertTrue(to_minimise.exists(), "stage 1 row must survive")
        self.assertFalse(to_minimise.ip_address, "stage 1 did not run")
        self.assertTrue(to_minimise.login.startswith(DIGEST_PREFIX))
        self.assertEqual(untouched.ip_address, "203.0.113.7", "in-window row touched")

    def test_the_purge_still_runs_when_minimisation_fails(self):
        """Deletion has its own deadline and its own validated preconditions. A
        transient stage-1 fault must not silently skip it for that run."""
        self.Param.set_param(RETENTION_PARAM, 180)
        self.Param.set_param(SKELETON_PARAM, 400)
        doomed = self._make_log(age_days=500, login="purge@ncollection.test")

        cron = self.env.ref("ncollection_auth.cron_gc_auth_log")

        def _boom():
            raise RuntimeError("simulated stage-1 fault")

        model = self.Log
        with patch.object(type(model), "_minimise_auth_log", staticmethod(_boom)):
            safe_eval(cron.code.strip(), {
                'model': model, 'env': self.env,
                'log': lambda *a, **k: None,
            }, mode="exec")

        doomed.invalidate_recordset()
        self.assertFalse(doomed.exists(),
                         "stage 1 failing must not suppress stage 2")

    # -- the two windows must stay coherent (regression cover) -------------

    def test_a_tiny_skeleton_is_refused_even_when_minimisation_is_off(self):
        """The floor must NOT depend on retention being enabled.

        The first version only compared skeleton against retention, so with
        retention disabled — a documented, legitimate state — skeleton could be
        set to 1 and delete day-old rows carrying FULL raw PII. That reopened
        the MIN_RETENTION_DAYS anti-forensics floor this module documents at
        length, reachable in two parameter writes by exactly the actor the floor
        exists to constrain.
        """
        self.Param.set_param(RETENTION_PARAM, 0)      # minimisation disabled
        self.Param.set_param(SKELETON_PARAM, 1)
        fresh_pii = self._make_log(age_days=5, login="evidence@ncollection.test")

        with self.assertRaises(UserError) as ctx:
            self.Log._gc_auth_log()

        self.assertIn("floor", str(ctx.exception))
        self.assertTrue(fresh_pii.exists(), "5-day-old evidence must survive")
        fresh_pii.invalidate_recordset()
        self.assertEqual(fresh_pii.ip_address, "203.0.113.7")

    def test_a_skeleton_equal_to_retention_is_refused(self):
        """`skeleton == retention` means minimise and delete on the same day —
        the flat delete #261 replaces, with every individual guard technically
        satisfied. The rule is a GAP, not merely non-inversion."""
        self.Param.set_param(RETENTION_PARAM, 180)
        self.Param.set_param(SKELETON_PARAM, 180)   # equal -> zero-day skeleton
        with self.assertRaises(UserError) as ctx:
            self.Log._gc_auth_log()
        self.assertIn("flat delete wearing two stages", str(ctx.exception))

    def test_an_upgraded_tenant_with_a_long_retention_still_purges(self):
        """The upgrade path. The new param lives in <data noupdate="1">, which
        Odoo skips on `-u`, so an existing tenant has no row for it. A tenant
        that had raised retention above the 400 default would then get
        skeleton(400) < retention, the guard would refuse on EVERY cron run, and
        Odoo deactivates a cron after 5 failures — silently switching off the
        delete stage on exactly the tenants that care most about retention.

        migrations/19.0.1.2.0/post-migrate.py seeds max(default, retention).
        This pins the outcome that migration must produce.
        """
        self.Param.search([("key", "=", SKELETON_PARAM)]).unlink()
        self.Param.set_param(RETENTION_PARAM, 500)
        # Exactly what migrations/19.0.1.2.0/post-migrate.py writes —
        # max(default, retention + floor). An earlier version of this test
        # mirrored max(default, retention), which yields skeleton == retention
        # for retention >= 400: a zero-day skeleton the coherence rule refuses.
        # That caught a real bug in the migration, not in the test.
        self.Param.set_param(SKELETON_PARAM, max(400, 500 + MIN_RETENTION_DAYS))

        ancient = self._make_log(age_days=9999, login="upgraded@ncollection.test")
        self.Log._gc_auth_log()          # must NOT raise
        self.assertFalse(ancient.exists())

    def test_a_login_containing_the_marker_is_still_minimised(self):
        """`not like` wraps its value in %...%, so the original domain asked
        "does not CONTAIN sha256:" while the marker is a PREFIX. A login with
        that substring anywhere would have been skipped forever."""
        self.Param.set_param(RETENTION_PARAM, 180)
        row = self._make_log(age_days=200, login="user+sha256:x@ncollection.test")
        self.Log._minimise_auth_log()
        row.invalidate_recordset()
        self.assertTrue(row.login.startswith(DIGEST_PREFIX))
        self.assertNotIn("@ncollection.test", row.login)

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
        self.Param.set_param(RETENTION_PARAM, 30)   # distinct: pins that
        self.Param.set_param(SKELETON_PARAM, 180)  # gc reads SKELETON
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
        self.Param.set_param(RETENTION_PARAM, 30)   # distinct: pins that
        self.Param.set_param(SKELETON_PARAM, 180)  # gc reads SKELETON
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
        self.Param.set_param(RETENTION_PARAM, 30)   # distinct: pins that
        self.Param.set_param(SKELETON_PARAM, 180)  # gc reads SKELETON
        older = self._make_log(age_days=900, login="older@ncollection.test")
        newer = self._make_log(age_days=400, login="newer@ncollection.test")

        self.Log._gc_auth_log()

        self.assertFalse(older.exists(), "the oldest row must go first")
        self.assertTrue(newer.exists())
