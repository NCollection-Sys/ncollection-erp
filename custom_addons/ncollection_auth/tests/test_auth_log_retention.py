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
from odoo.tests import TransactionCase, tagged

from odoo.addons.ncollection_auth.models.auth_log import (
    DEFAULT_RETENTION_DAYS,
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
        """The window is actually read, not hardcoded."""
        self.Param.set_param(RETENTION_PARAM, 7)
        recent = self._make_log(age_days=30, login="thirty@ncollection.test")
        self.Log._gc_auth_log()
        self.assertFalse(
            recent.exists(),
            "a 30-day-old row must be purged under a 7-day window",
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

    def test_garbage_param_falls_back_to_the_default(self):
        """Garbage must NOT read as 0.

        Treating an unparseable value as zero would disable the purge — a
        retention policy silently switched off by a typo is the exact failure
        this ticket exists to prevent.
        """
        self.Param.set_param(RETENTION_PARAM, "not-a-number")
        self.assertEqual(self.Log._retention_days(), DEFAULT_RETENTION_DAYS)

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
