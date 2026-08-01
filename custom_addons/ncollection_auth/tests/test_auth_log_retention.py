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

from odoo.addons.ncollection_auth.models.auth_log import (
    DEFAULT_RETENTION_DAYS,
    MIN_RETENTION_DAYS,
    RETENTION_PARAM,
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
        """The core contract, both halves asserted in one place."""
        self.Param.set_param(RETENTION_PARAM, 180)
        stale = self._make_log(age_days=400, login="stale@ncollection.test")
        edge_old = self._make_log(age_days=181, login="edge_old@ncollection.test")
        edge_new = self._make_log(age_days=179, login="edge_new@ncollection.test")
        fresh = self._make_log(age_days=0, login="fresh@ncollection.test")

        deleted = self.Log._gc_auth_log()

        self.assertGreaterEqual(deleted, 2, "both out-of-window rows should go")
        self.assertFalse(stale.exists(), "a 400-day-old row must be purged")
        self.assertFalse(edge_old.exists(), "a 181-day-old row must be purged")
        self.assertTrue(edge_new.exists(), "a 179-day-old row must be kept")
        self.assertTrue(fresh.exists(), "today's row must be kept")

    def test_a_shorter_window_purges_more(self):
        """The window is actually read, not hardcoded at the default.

        Uses 60 days rather than something tighter because MIN_RETENTION_DAYS
        refuses anything under 30 — a row that would survive the 180-day
        default must still be purged under a legitimately shorter one.
        """
        self.Param.set_param(RETENTION_PARAM, 60)
        recent = self._make_log(age_days=90, login="ninety@ncollection.test")
        self.Log._gc_auth_log()
        self.assertFalse(
            recent.exists(),
            "a 90-day-old row must be purged under a 60-day window",
        )

    # -- the off switch -----------------------------------------------------

    def test_zero_disables_the_purge(self):
        """`<= 0` means disabled — the documented escape hatch."""
        self.Param.set_param(RETENTION_PARAM, 0)
        ancient = self._make_log(age_days=9999, login="ancient@ncollection.test")
        self.assertEqual(self.Log._gc_auth_log(), 0)
        self.assertTrue(ancient.exists(), "nothing may be deleted when disabled")

    def test_negative_disables_the_purge(self):
        self.Param.set_param(RETENTION_PARAM, -1)
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
        self.Param.set_param(RETENTION_PARAM, 180)
        self._make_log(age_days=400)
        first = self.Log._gc_auth_log()
        second = self.Log._gc_auth_log()
        self.assertGreaterEqual(first, 1)
        self.assertEqual(second, 0, "re-running must delete nothing further")

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
        """
        self.env.cr.execute(
            "SELECT indexdef FROM pg_indexes WHERE tablename = %s",
            ("ncollection_auth_log",),
        )
        defs = " ".join(row[0] for row in self.env.cr.fetchall())
        self.assertIn(
            "create_date", defs,
            "expected an index covering create_date on ncollection_auth_log",
        )
