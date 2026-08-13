#!/usr/bin/env python3
"""Keep the counts in our load-bearing documents true (#405, #408).

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
is ENFORCED by scripts/ci/check_role_matrix.py. These documents never got it.

TWO DOCUMENTS, ONE GUARD (#408). CLAUDE.md had the same defect and worse: it is
auto-loaded into EVERY session, so a wrong statement there is repeated to every
future reader before they look at anything. It claimed "nine" pre-push gates
(11), listed 4 custom addons (16), and called ncollection_core "near-empty" and
ncollection_saas an "empty skeleton" — 8,647 and 9,856 lines respectively.

A second copy of this script would have been the drift-by-duplication this repo
keeps being bitten by (#243, #374): two guards, one of them quietly falling
behind. The marker machinery and the measurements are generic, so both documents
share them.

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
DOCS = (
    REPO / "docs" / "markdown" / "TESTING_STRATEGY.md",
    REPO / "CLAUDE.md",
)

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
    # #408 — CLAUDE.md's own claims. The gate count went stale the same DAY it
    # was written: #394 wrote "nine", #405 added two more gates hours later.
    m["prepush_gates"] = len(re.findall(
        r'^\s*run_gate\s+"', (REPO / ".githooks" / "pre-push").read_text(
            encoding="utf-8"), re.MULTILINE))
    m["custom_addons"] = len([d for d in (REPO / "custom_addons").iterdir()
                              if d.is_dir() and (d / "__manifest__.py").exists()])

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


def _check_one(doc, actual: dict[str, int], write: bool) -> tuple[int, list[str]]:
    """Verify (or repair) one document. Returns (markers_seen, problems)."""
    try:
        text = doc.read_text(encoding="utf-8")
    except OSError as exc:
        raise MeasureError("%s is unreadable (%s), so its counts cannot be "
                           "verified" % (doc, exc))

    marked = {m.group("key"): int(m.group("value").replace(",", ""))
              for m in MARKER_RE.finditer(text)}
    if not marked:
        raise MeasureError(
            "no <!--count:*--> markers found in %s. Either the document lost "
            "them or this guard is aimed at the wrong file; either way it is "
            "verifying nothing" % doc.name)

    unknown = sorted(set(marked) - set(actual))
    if unknown:
        raise MeasureError(
            "%s marks count(s) this guard cannot measure: %s. Add a "
            "measurement or remove the marker — a marker nothing checks is "
            "worse than no marker" % (doc.name, ", ".join(unknown)))

    if write:
        def _fix(m: re.Match) -> str:
            return "**%d**<!--count:%s-->" % (actual[m.group("key")],
                                              m.group("key"))
        doc.write_text(MARKER_RE.sub(_fix, text), encoding="utf-8")
        return len(marked), []

    return len(marked), [
        "  %-22s %s says %s, tree says %s"
        % (key, doc.name, marked[key], actual[key])
        for key in sorted(marked) if marked[key] != actual[key]
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true",
                    help="update the documents' counts in place")
    args = ap.parse_args()

    try:
        actual = measure()
    except MeasureError as exc:
        print("REFUSING: %s" % exc, file=sys.stderr)
        return 1

    total, problems = 0, []
    for doc in DOCS:
        try:
            seen, probs = _check_one(doc, actual, args.write)
        except MeasureError as exc:
            print("REFUSING: %s." % exc, file=sys.stderr)
            return 1
        total += seen
        problems += probs

    if args.write:
        print("doc counts written (%d markers across %d documents)"
              % (total, len(DOCS)))
        return 0

    if problems:
        print("documents are out of date:", file=sys.stderr)
        for p in problems:
            print(p, file=sys.stderr)
        print("\nRun: python3 scripts/ci/check_doc_counts.py --write\n"
              "These are the documents a reader trusts for what this repo is "
              "and what a green result proves. CLAUDE.md is loaded into every "
              "session, so a wrong number there is repeated to every future "
              "reader (#405, #408).", file=sys.stderr)
        return 1

    print("doc counts clean (%d markers across %d documents verified)"
          % (total, len(DOCS)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
