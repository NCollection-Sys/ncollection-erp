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

# The #363 case, in Odoo 19's REAL description format.
#
# The #363 commit elided the description as "skipped ... : Chrome executable not
# found", and the first version of this fixture filled the gap with unittest's
# shape — `method (package.Class)`. Odoo does not emit that:
# OdooTestResult.getDescription returns "Class.method". Building a fixture from
# an assumed format is how the parser came to target a shape that never occurs,
# so this is now taken from the shipped source rather than reconstructed.
TOURS_LOG = (
    "2026-08-06 18:59:12,001 1 INFO t1 odoo.addons.ncollection_account_dashboard"
    ".tests.test_dashboard_tours: skipped TestDashboardTours.test_ceo_tour : "
    "Chrome executable not found\n"
    "2026-08-06 18:59:12,002 1 INFO t1 odoo.addons.ncollection_account_dashboard"
    ".tests.test_dashboard_tours: skipped TestDashboardTours.test_finance_tour : "
    "websocket-client module is not installed\n"
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
        allow = ("ncollection_account_dashboard.TestDashboardTours.test_ceo_tour"
                 "  # deliberate, for this test\n")
        log = "".join(ln + "\n" for ln in TOURS_LOG.splitlines()
                      if "test_finance_tour" not in ln)
        self.assertEqual(self.run_gate(log, allow), 0)


class TestIdentityNotReasonText(GateTestCase):
    """The central design decision, asserted rather than described.

    Keying on the reason string would allowlist the dangerous case: our suite
    already emits "sale is not installed on this database", so a text match
    would wave through the day `sale` drops out of the install list.
    """

    def test_the_same_reason_on_a_different_test_still_fails(self):
        allow = "mod.TestKpi.test_sales_kpi  # deliberate absent-module assertion\n"
        log = ("INFO db odoo.addons.mod.tests.t: skipped TestKpi.test_sales_kpi : "
               "sale is not installed on this database\n"
               "INFO db odoo.addons.mod.tests.t: skipped TestOther.test_other : "
               "sale is not installed on this database\n")
        self.assertEqual(
            self.run_gate(log, allow), 1,
            "a NEW test skipping for an already-known reason must still fail — "
            "that is coverage quietly disappearing")

    def test_allowlisting_is_not_substring_matching(self):
        """`TestKpi.test_sales` must not admit `TestKpi.test_sales_extended`."""
        allow = "mod.TestKpi.test_sales  # deliberate\n"
        log = ("INFO db odoo.addons.mod.tests.t: skipped "
               "TestKpi.test_sales_extended : sale is not installed\n")
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


class TestSubtestsDoNotCollide(GateTestCase):
    """The CRITICAL from review, pinned.

    Odoo renders a subtest skip as `Subtest Class.method (param='x')`. An
    earlier `normalise()` matched the STANDARD LIBRARY shape instead, so every
    description fell to a `split()[0]` fallback — and for a subtest that is the
    literal word "Subtest". Every subtest skip in the repo collapsed to one
    identity, so a single allowlist entry would have masked all of them
    forever, environmental ones included. There are already 19 `subTest` call
    sites in this suite.
    """

    # BOTH IN THE SAME ADDON, deliberately. An earlier version put them in two
    # addons — and the addon prefix then told them apart even with the subtest
    # bug restored, so the test passed against the very defect it was written
    # for. Same addon isolates the normalisation, which is what is under test.
    SUB_A = ("INFO db odoo.addons.ncollection_core.tests.test_kpi: skipped "
             "Subtest TestAlpha.test_alpha (method='avg') : KPI not seeded\n")
    SUB_B = ("INFO db odoo.addons.ncollection_core.tests.test_other: skipped "
             "Subtest TestBeta.test_beta (model='stock') : Chrome not found\n")

    def test_a_subtest_does_not_normalise_to_the_word_subtest(self):
        (identity, _), = check_skips.find_skips(self.SUB_A)
        self.assertNotEqual(identity, "Subtest")
        self.assertIn("TestAlpha.test_alpha", identity)

    def test_two_different_subtests_get_two_identities(self):
        ids = {i for log in (self.SUB_A, self.SUB_B)
               for i, _ in check_skips.find_skips(log)}
        self.assertEqual(len(ids), 2,
                         "distinct subtests must not share an allowlist key")

    def test_allowlisting_one_subtest_does_not_admit_another(self):
        """The consequence, end to end: the environmental skip must still fail
        even though a deliberate subtest skip is allowlisted."""
        allow = "ncollection_core.TestAlpha.test_alpha  # deliberate\n"
        self.assertEqual(self.run_gate(self.SUB_A + self.SUB_B, allow), 1)


class TestAddonPrefix(GateTestCase):
    def test_the_addon_comes_from_the_logger(self):
        log = ("INFO db odoo.addons.ncollection_billing.tests.test_billing: "
               "skipped TestBilling.test_x : deliberate\n")
        (identity, _), = check_skips.find_skips(log)
        self.assertEqual(identity, "ncollection_billing.TestBilling.test_x")

    def test_same_class_and_method_in_two_addons_do_not_collide(self):
        log = ("INFO db odoo.addons.mod_a.tests.t: skipped TestBasic.test_smoke : a\n"
               "INFO db odoo.addons.mod_b.tests.t: skipped TestBasic.test_smoke : b\n")
        self.assertEqual(len({i for i, _ in check_skips.find_skips(log)}), 2)

    def test_a_line_without_a_logger_still_yields_an_identity(self):
        """Fail-safe: an unrecognised log shape must still produce a stable,
        reportable key rather than crashing or silently dropping the skip."""
        (identity, _), = check_skips.find_skips("skipped TestC.test_x : why\n")
        self.assertEqual(identity, "TestC.test_x")


class TestAllowlistHygiene(GateTestCase):
    def test_an_entry_without_a_reason_is_rejected(self):
        with self.assertRaises(SystemExit):
            self.run_gate("0 failed, 0 error(s) of 1 tests\n",
                          "SomeClass.some_test\n")


class TestParsing(unittest.TestCase):
    def test_odoos_two_real_description_shapes(self):
        """Read from odoo/tests/result.py::getDescription in the pinned image,
        not from unittest's docs — the two differ, and assuming the stdlib
        shape is what caused the subtest collision above."""
        self.assertEqual(check_skips.normalise("TestC.test_x"), "TestC.test_x")
        self.assertEqual(
            check_skips.normalise("Subtest TestC.test_x (i=1)"), "TestC.test_x")

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
