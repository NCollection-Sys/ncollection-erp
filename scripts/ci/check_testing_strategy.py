#!/usr/bin/env python3
"""Keep docs/markdown/TESTING_STRATEGY.md's counts true (#405).

WHY THIS EXISTS. CLAUDE.md calls that document authoritative for "what a green
result actually proves". #394 re-measured every count in it and dated the
result. TWELVE HOURS and three merges later it was wrong again:

    e2e        doc: 10 specs / 21 tests    actual: 11 specs / 27 tests
    python     doc: 961 test methods       actual: 973
    invariants doc: 9 rules                actual: 10

#394 was not careless — hand-written counts simply cannot survive normal
merging when nothing checks them. Same shape as #382, where hooks.py claimed
docs/ROLE_MATRIX.md was enforced and nothing enforced it, and the same fix.

The repo already solves this twice: PROGRESS.md is GENERATED, and ROLE_MATRIX.md
is ENFORCED by scripts/ci/check_role_matrix.py. This document just never got it.

HOW IT WORKS. Each count in the prose carries an invisible marker:

    **973**<!--count:test_methods-->

HTML comments do not render, so the document reads exactly as before. The guard
finds each marker, compares it to a freshly measured value, and fails on a
mismatch. `--write` updates them in place.

WHAT IT DELIBERATELY DOES NOT CHECK, because pretending would be this ticket's
own defect one level up:

  * the e2e TEST count (as opposed to spec files) — needs Playwright to run;
  * the odoo test count — `make test` reports 977 where a `def test_` grep
    gives 973, because subtests are counted separately. Two different true
    numbers; only the grep is derivable here;
  * all durations.

Those stay DATED MEASUREMENTS, labelled in the document with the footnote
convention it already uses. A guard that claimed them would be lying quietly.

Exit 0 = every marked count matches. Exit 1 = one does not, or a marker is
missing, or a value could not be measured.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DOC = REPO / "docs" / "markdown" / "TESTING_STRATEGY.md"

MARKER_RE = re.compile(r"\*\*(?P<value>[\d,]+)\*\*<!--count:(?P<key>\w+)-->")


class MeasureError(Exception):
    """A count could not be measured. Never treated as zero."""


def _sh(cmd: str) -> str:
    out = subprocess.run(cmd, shell=True, cwd=REPO, capture_output=True, text=True)
    if out.returncode != 0:
        raise MeasureError("%s -> exit %d: %s" % (cmd, out.returncode, out.stderr.strip()))
    return out.stdout.strip()


def _count_lines(cmd: str) -> int:
    val = _sh(cmd)
    return len([ln for ln in val.splitlines() if ln.strip()])


def measure() -> dict[str, int]:
    """Every statically derivable figure, measured from the tree."""
    m: dict[str, int] = {}

    m["test_files"] = _count_lines(
        "find custom_addons -path '*/tests/test_*.py'")
    m["test_methods"] = sum(
        len(re.findall(r"^\s*def test_", p.read_text(encoding="utf-8", errors="ignore"),
                       re.MULTILINE))
        for p in REPO.glob("custom_addons/*/tests/test_*.py"))
    m["verify_scripts"] = _count_lines(
        "find . -name 'verify_*.sh' -not -path './node_modules/*'")
    m["verify_all_suites"] = len(re.findall(
        r"^\t@\$\(MAKE\) --no-print-directory .*-verify",
        (REPO / "Makefile").read_text(encoding="utf-8"), re.MULTILINE))
    m["e2e_specs"] = len(list((REPO / "e2e" / "tests").glob("*.spec.ts")))
    m["guard_selftests"] = len(list((REPO / "scripts" / "ci").glob("test_*.py")))

    inv = (REPO / "scripts" / "ci" / "invariants.py").read_text(encoding="utf-8")
    found = re.search(r"^RULE_COUNT = (\d+)", inv, re.MULTILINE)
    if not found:
        raise MeasureError("RULE_COUNT not found in invariants.py")
    m["invariants_rules"] = int(found.group(1))

    gate = (REPO / "scripts" / "ci" / "pylint_gate.sh").read_text(encoding="utf-8")
    found = re.search(r"^PYLINT_BASELINE=(\d+)", gate, re.MULTILINE)
    if not found:
        raise MeasureError("PYLINT_BASELINE not found in pylint_gate.sh")
    m["pylint_baseline"] = int(found.group(1))

    for key, value in m.items():
        if value <= 0:
            raise MeasureError(
                "%s measured as %d — a zero count means the measurement broke, "
                "not that the estate is empty" % (key, value))
    return m


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true",
                    help="update the document's counts in place")
    args = ap.parse_args()

    try:
        actual = measure()
    except MeasureError as exc:
        print("REFUSING: %s" % exc, file=sys.stderr)
        return 1

    try:
        text = DOC.read_text(encoding="utf-8")
    except OSError as exc:
        print("REFUSING: %s is unreadable (%s), so its counts cannot be "
              "verified." % (DOC, exc), file=sys.stderr)
        return 1

    marked = {m.group("key"): int(m.group("value").replace(",", ""))
              for m in MARKER_RE.finditer(text)}
    if not marked:
        print("REFUSING: no <!--count:*--> markers found in %s. Either the "
              "document lost them or this guard is aimed at the wrong file; "
              "either way it is verifying nothing." % DOC.name, file=sys.stderr)
        return 1

    unknown = sorted(set(marked) - set(actual))
    if unknown:
        print("REFUSING: the document marks count(s) this guard cannot "
              "measure: %s. Add a measurement or remove the marker — a marker "
              "nothing checks is worse than no marker."
              % ", ".join(unknown), file=sys.stderr)
        return 1

    if args.write:
        def _fix(m: re.Match) -> str:
            return "**%d**<!--count:%s-->" % (actual[m.group("key")], m.group("key"))
        DOC.write_text(MARKER_RE.sub(_fix, text), encoding="utf-8")
        print("testing-strategy counts written (%d markers)" % len(marked))
        return 0

    problems = [
        "  %-18s document says %s, tree says %s" % (key, marked[key], actual[key])
        for key in sorted(marked) if marked[key] != actual[key]
    ]
    if problems:
        print("docs/markdown/TESTING_STRATEGY.md is out of date:", file=sys.stderr)
        for p in problems:
            print(p, file=sys.stderr)
        print("\nRun: python3 scripts/ci/check_testing_strategy.py --write\n"
              "This document is what CLAUDE.md points at for 'what a green "
              "result actually proves'. A stale answer to that is worse than "
              "no answer, because it is trusted (#405).", file=sys.stderr)
        return 1

    print("testing-strategy counts clean (%d markers verified)" % len(marked))
    return 0


if __name__ == "__main__":
    sys.exit(main())
