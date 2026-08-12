#!/usr/bin/env python3
"""Tests for the testing-strategy count guard (#405).

The guard exists because hand-written counts in TESTING_STRATEGY.md went stale
within twelve hours of #394 re-measuring them. These tests exist because a
guard that cannot fail is the same problem one level up — this repo has shipped
several of those (#330, #348, #363, #381), and #403 caught two more that had
already passed CI while measuring nothing.

Run standalone (no pytest dependency in CI):

    python3 scripts/ci/test_check_testing_strategy.py
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_testing_strategy as C  # noqa: E402


class GuardTestCase(unittest.TestCase):

    def setUp(self):
        self._doc = C.DOC
        self.tmp = Path(tempfile.mkdtemp()) / "TESTING_STRATEGY.md"
        C.DOC = self.tmp

    def tearDown(self):
        C.DOC = self._doc

    def write(self, text):
        self.tmp.write_text(text, encoding="utf-8")


class TestMarkerParsing(GuardTestCase):

    def test_matching_counts_are_clean(self):
        actual = C.measure()
        self.write("rules **%d**<!--count:invariants_rules--> here\n"
                   % actual["invariants_rules"])
        self.assertEqual(C.main(), 0)

    def test_a_drifted_count_fails(self):
        actual = C.measure()
        self.write("rules **%d**<!--count:invariants_rules--> here\n"
                   % (actual["invariants_rules"] + 1))
        self.assertEqual(C.main(), 1)

    def test_a_document_with_no_markers_refuses(self):
        """The guard must not be silently disabled by deleting its markers."""
        self.write("# Testing strategy\n\nNo markers at all.\n")
        self.assertEqual(C.main(), 1)

    def test_a_marker_the_guard_cannot_measure_refuses(self):
        """A marker nothing checks is worse than no marker."""
        self.write("**3**<!--count:number_of_unicorns-->\n")
        self.assertEqual(C.main(), 1)

    def test_an_unreadable_document_refuses(self):
        C.DOC = Path("/nonexistent/TESTING_STRATEGY.md")
        self.assertEqual(C.main(), 1)

    def test_write_mode_repairs_a_drifted_count(self):
        actual = C.measure()
        self.write("rules **999**<!--count:invariants_rules--> here\n")
        sys.argv = ["check_testing_strategy.py", "--write"]
        try:
            self.assertEqual(C.main(), 0)
        finally:
            sys.argv = ["check_testing_strategy.py"]
        self.assertIn("**%d**<!--count:invariants_rules-->"
                      % actual["invariants_rules"],
                      self.tmp.read_text(encoding="utf-8"))


class TestMeasurement(unittest.TestCase):

    def test_every_measured_value_is_positive(self):
        """A zero would mean the measurement broke, not that the estate is
        empty — measure() raises rather than reporting zero."""
        for key, value in C.measure().items():
            with self.subTest(metric=key):
                self.assertGreater(value, 0)

    def test_the_real_document_is_currently_in_sync(self):
        """The guard's own subject, checked for real rather than in a fixture."""
        self.assertEqual(C.main(), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
