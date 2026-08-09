#!/usr/bin/env python3
"""Fail a test run that SKIPPED a test nobody expected it to skip.

WHY THIS EXISTS. Odoo reports a skipped test inside its pass count:

    skipped ... : Chrome executable not found
    odoo.tests.result: 0 failed, 0 error(s) of 4 tests

That line is green. Nothing ran. Four browser tours were written against
correct selectors and would have reported success forever in CI, because the
`odoo:19` image has no browser and `HttpCase` responds to that by skipping
(#363, branch feature/363-dashboard-tours, which was never merged for exactly
this reason).

`ci.yml` already learned this once the hard way — its own comment says "a
skipped test is not a passing test", written when `hr` had to be added to the
install list to stop the KPI tests skipping. Nothing detected it then either.

THE HAZARD, STATED PRECISELY. An ENVIRONMENTAL skip (a missing browser, a
missing module, a missing library) and a DELIBERATE one (a test asserting the
absent-module case on purpose) are indistinguishable in Odoo's output: both are
`skipped <test> : <reason>`. So "how many skips are there" cannot be answered
by reading the log, and a coverage loss looks exactly like a design choice.

WHY THE ALLOWLIST IS KEYED ON TEST IDENTITY, NOT ON REASON TEXT. This is the
whole design, and the obvious alternative is actively wrong. The reasons our
suite already emits are:

    "sale is not installed on this database"
    "hr is not installed on this database"
    "stock is not installed on this database"
    "mis_builder not installed in this run"

Allowlisting those STRINGS would allowlist precisely the dangerous case — the
day `sale` drops out of the install list, three tests go quiet and the gate
says fine. Keyed on identity, a test that starts skipping is a new entry the
gate has never seen, and it fails.

WHAT IS *NOT* AN ERROR. An expected skip that did not happen is reported and
does not fail: it usually means a module became available and the test now
runs, which is an improvement, and the matrix runner (#365) deliberately
installs different module sets per job. Stale entries are noise; a silent
coverage hole is not.

Usage:
    python scripts/ci/check_skips.py odoo-test.log
    python scripts/ci/check_skips.py --allowlist scripts/ci/expected_skips.txt LOG

Exit 0 = every skip was expected. Exit 1 = at least one was not.
"""

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_ALLOWLIST = REPO_ROOT / "scripts" / "ci" / "expected_skips.txt"

# Odoo emits exactly this, from odoo/tests/result.py::addSkip:
#     self.log(logging.INFO, 'skipped %s : %s', self.getDescription(test), reason)
# The description shape is Odoo's business and has changed between versions, so
# capture it loosely and normalise afterwards rather than pinning a format.
SKIP_RE = re.compile(r"\bskipped\s+(?P<desc>.+?)\s+:\s+(?P<reason>.*)$")
# Odoo logs the addon in the logger name and deliberately keeps it OUT of the
# description ("since we have the module name in the logger, this will avoid to
# duplicate module info in log line" — odoo/tests/result.py). Recovering it here
# makes the identity `addon.Class.method`, so two modules that happen to name a
# class and method identically cannot share an allowlist entry.
LOGGER_ADDON_RE = re.compile(r"odoo\.addons\.(?P<addon>\w+)[.\s:]")


# Odoo 19's OdooTestResult.getDescription, read from the shipped source rather
# than assumed, emits exactly two shapes:
#
#     TestCase   -> "Class.method"
#     _SubTest   -> "Subtest Class.method (param='x')"
#
# NOT the standard library's "method (package.module.Class)". An earlier version
# of this file matched the stdlib shape; that branch never fired on a real Odoo
# log, and every description fell through to a `split()[0]` fallback which
# happened to be right for the first shape and CATASTROPHICALLY wrong for the
# second — every subtest skip in the repo collapsed to the single identity
# "Subtest". One allowlist entry would then have masked every subtest skip
# forever, environmental ones included: the exact failure this gate exists to
# prevent, reproduced inside the gate. There are already 19 `subTest` call sites
# in the suite. Found by review, not by the tests.
SUBTEST_RE = re.compile(r"^Subtest\s+(?P<ident>[\w.]+\.\w+)")
TESTCASE_RE = re.compile(r"^(?P<ident>\w+\.\w+)\s*$")


def normalise(description: str, addon: str = "") -> str:
    """Reduce Odoo's test description to a stable identity.

    Returns `addon.Class.method` when the addon is known, else `Class.method`.

    The subtest's PARAMETERS are deliberately dropped. Keying on them would make
    an allowlist entry expire the moment a loop's values changed, and the
    granularity that matters is the test, not the iteration — the same
    granularity the non-subtest shape gives.
    """
    description = description.strip()
    m = SUBTEST_RE.match(description) or TESTCASE_RE.match(description)
    ident = m.group("ident") if m else (
        description.split()[0] if description.split() else description)
    return f"{addon}.{ident}" if addon else ident


def load_allowlist(path: Path) -> dict[str, str]:
    """`identity  # why this skip is acceptable` per line; blanks and #-lines ignored."""
    allowed: dict[str, str] = {}
    if not path.exists():
        return allowed
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        identity, _, why = line.partition("#")
        identity, why = identity.strip(), why.strip()
        if not why:
            # An entry with no justification is an assertion nobody made. The
            # whole point of the allowlist is that adding a line is a CLAIM the
            # skip is deliberate; an unexplained line is indistinguishable from
            # someone silencing a failure.
            raise SystemExit(
                f"skip-gate: {path.name}: '{identity}' has no reason.\n"
                f"           Format is `Class.method  # why this skip is "
                f"acceptable`.")
        allowed[identity] = why
    return allowed


def find_skips(text: str) -> list[tuple[str, str]]:
    """Every (identity, reason) the log reports as skipped."""
    found = []
    for line in text.splitlines():
        m = SKIP_RE.search(line)
        if not m:
            continue
        addon_m = LOGGER_ADDON_RE.search(line)
        addon = addon_m.group("addon") if addon_m else ""
        found.append((normalise(m.group("desc"), addon), m.group("reason").strip()))
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", help="the Odoo test log to inspect")
    parser.add_argument("--allowlist", default=str(DEFAULT_ALLOWLIST))
    args = parser.parse_args()

    log_path = Path(args.log)
    if not log_path.exists():
        # Fail closed: a missing log means the gate verified nothing, and
        # reporting "clean" over an absent file is how a guard becomes theatre.
        print(f"skip-gate: cannot read {log_path} — failing closed.", file=sys.stderr)
        return 1

    allowed = load_allowlist(Path(args.allowlist))
    skips = find_skips(log_path.read_text(encoding="utf-8", errors="ignore"))

    unexpected = [(i, r) for i, r in skips if i not in allowed]
    seen = {i for i, _ in skips}
    stale = [i for i in allowed if i not in seen]

    for identity in sorted(stale):
        print(f"skip-gate: note — expected skip did not occur: {identity}\n"
              f"           (usually means it now RUNS; remove it from the "
              f"allowlist when that is settled)")

    if not unexpected:
        print(f"skip-gate: clean ({len(skips)} skip(s), all expected).")
        return 0

    print(f"\nskip-gate: {len(unexpected)} UNEXPECTED skip(s) — a skipped test "
          f"is not a passing test.\n", file=sys.stderr)
    for identity, reason in unexpected:
        print(f"  ✗ {identity}\n      reason: {reason}", file=sys.stderr)
    print(
        "\nOdoo counts a skipped test as passing, so this run may report "
        "'0 failed' while\ncovering nothing. Either fix the environment so the "
        "test RUNS (the usual answer —\nsee #363: four browser tours skipped "
        "silently because the image has no browser),\nor add the test to "
        f"{Path(args.allowlist).name} with a one-line reason if the skip is "
        "deliberate.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
