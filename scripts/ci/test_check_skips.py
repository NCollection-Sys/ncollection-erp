#!/usr/bin/env python3
"""Tests for the skip gate (#363 follow-up).

The gate exists because a skipped Odoo test is counted as a passing one. These
tests exist because a gate that cannot fail is the same problem one level up —
this repo has shipped three of those already (#330, #348, #311), each caught by
mutation rather than by reading.

Run standalone (no pytest dependency in CI):

    python3 scripts/ci/test_check_skips.py
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_skips  # noqa: E402

# Verbatim from the #363 branch's own run — the case the gate was built for.
TOURS_LOG = (
    "2026-08-06 18:59:12,001 1 INFO t1 odoo.addons.x.tests.test_dashboard_tours: "
    "skipped test_ceo_tour (odoo.addons.x.tests.test_dashboard_tours."
    "TestDashboardTours) : Chrome executable not found\n"
    "2026-08-06 18:59:12,002 1 INFO t1 odoo.addons.x.tests.test_dashboard_tours: "
    "skipped test_finance_tour (odoo.addons.x.tests.test_dashboard_tours."
    "TestDashboardTours) : websocket-client module is not installed\n"
    "2026-08-06 18:59:12,003 1 INFO t1 odoo.tests.result: "
    "0 failed, 0 error(s) of 4 tests\n"
)


class GateTestCase(unittest.TestCase):
    def run_gate(self, log_text, allowlist_text=""):
        """Run the gate over a log, returning its exit code."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        log = root / "odoo-test.log"
        log.write_text(log_text, encoding="utf-8")
        allow = root / "expected_skips.txt"
        allow.write_text(allowlist_text, encoding="utf-8")
        argv = sys.argv
        sys.argv = ["check_skips.py", str(log), "--allowlist", str(allow)]
        try:
            return check_skips.main()
        finally:
            sys.argv = argv


class TestTheCaseItWasBuiltFor(GateTestCase):
    def test_the_tours_log_fails(self):
        """The real #363 run: two skips inside '0 failed ... of 4 tests'."""
        self.assertEqual(self.run_gate(TOURS_LOG), 1)

    def test_a_clean_run_passes(self):
        self.assertEqual(self.run_gate(
            "odoo.tests.result: 0 failed, 0 error(s) of 869 tests\n"), 0)

    def test_an_allowlisted_skip_passes(self):
        allow = "TestDashboardTours.test_ceo_tour  # deliberate, for this test\n"
        log = TOURS_LOG.replace(
            "skipped test_finance_tour (odoo.addons.x.tests."
            "test_dashboard_tours.TestDashboardTours) : "
            "websocket-client module is not installed\n", "")
        self.assertEqual(self.run_gate(log, allow), 0)


class TestIdentityNotReasonText(GateTestCase):
    """The central design decision, asserted rather than described.

    Keying on the reason string would allowlist the dangerous case: our suite
    already emits "sale is not installed on this database", so a text match
    would wave through the day `sale` drops out of the install list.
    """

    def test_the_same_reason_on_a_different_test_still_fails(self):
        allow = "TestKpi.test_sales_kpi  # deliberate absent-module assertion\n"
        log = ("INFO db mod: skipped test_sales_kpi (a.b.TestKpi) : "
               "sale is not installed on this database\n"
               "INFO db mod: skipped test_other (a.b.TestOther) : "
               "sale is not installed on this database\n")
        self.assertEqual(
            self.run_gate(log, allow), 1,
            "a NEW test skipping for an already-known reason must still fail — "
            "that is coverage quietly disappearing")

    def test_allowlisting_is_not_substring_matching(self):
        """`TestKpi.test_sales` must not admit `TestKpi.test_sales_extended`."""
        allow = "TestKpi.test_sales  # deliberate\n"
        log = ("INFO db mod: skipped test_sales_extended (a.b.TestKpi) : "
               "sale is not installed\n")
        self.assertEqual(self.run_gate(log, allow), 1)


class TestFailureModes(GateTestCase):
    def test_a_missing_log_fails_closed(self):
        """Reporting clean over an absent file is how a guard becomes theatre —
        the same fail-closed choice architecture_guard makes when its diff
        cannot be computed."""
        argv = sys.argv
        sys.argv = ["check_skips.py", "/nonexistent/odoo-test.log"]
        try:
            self.assertEqual(check_skips.main(), 1)
        finally:
            sys.argv = argv

    def test_a_stale_allowlist_entry_is_a_note_not_a_failure(self):
        """An expected skip that did not happen usually means it now RUNS.
        Failing on that would punish an improvement, and the matrix runner
        (#365) installs different module sets per job by design."""
        allow = "TestGone.test_vanished  # was deliberate\n"
        self.assertEqual(self.run_gate("0 failed, 0 error(s) of 3 tests\n", allow), 0)


class TestParsing(unittest.TestCase):
    def test_both_description_shapes_normalise_to_class_dot_method(self):
        self.assertEqual(
            check_skips.normalise("test_x (odoo.addons.m.tests.t.TestC)"),
            "TestC.test_x")
        self.assertEqual(check_skips.normalise("TestC.test_x"), "TestC.test_x")

    def test_the_reason_is_captured_whole(self):
        skips = check_skips.find_skips(TOURS_LOG)
        self.assertEqual(len(skips), 2)
        self.assertIn("Chrome executable not found", [r for _, r in skips])

    def test_an_ordinary_log_line_containing_the_word_is_not_a_skip(self):
        """`skipped` appears in prose too; the ' : ' separator is what makes it
        a real skip record from odoo/tests/result.py."""
        self.assertEqual(
            check_skips.find_skips("INFO: 3 modules skipped during upgrade\n"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
