#!/usr/bin/env python3
"""Derive the test matrix (install list + test tags) from ci.yml.

WHY THIS EXISTS AT ALL
----------------------
`make test` needs the same module list and `--test-tags` that CI runs with. The
obvious implementation — paste the list into the Makefile — reintroduces exactly
the CI-vs-local drift that #267 removed for the pylint gate: two copies, one of
which is edited and the other silently left behind. The local run would then be
green against a stale matrix and prove nothing about CI.

So ci.yml stays the single source of truth and the local runner *derives* from
it. That is not merely convenient — `rule_ci_module_coverage` in
scripts/ci/invariants.py already fails the build if any `custom_addons` module is
missing from ci.yml's `-i` or `--test-tags`. Deriving from the list that guard
polices makes "local runs what CI runs" true by construction rather than by
discipline.

WHY THE PARSER IS IMPORTED, NOT REIMPLEMENTED
---------------------------------------------
Two parsers drift for the same reason two lists do. `_ci_flag_values` already
handles the hard parts — line continuations, and discarding operands of unrelated
flags so a stray `grep -i` cannot masquerade as the install list. Reimplementing
that here would mean the guard and the runner could disagree about what ci.yml
says, which is the worst of both worlds: a green local run against a matrix CI
never uses.

Usage:
    ci_matrix.py --modules     # comma-separated -i list
    ci_matrix.py --tags        # comma-separated --test-tags list
    ci_matrix.py --self-test   # verify extraction still works
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "ci"))

# Imported after the path insert, hence the noqa. Private by name but not by
# intent: it is the repo's one CI-flag parser, and the alternative is a second
# one that can disagree with it.
from invariants import (  # noqa: E402
    CI_EXEMPT_MODULES,
    CI_WORKFLOW,
    _ci_flag_values,
)


def _workflow_text() -> str:
    path = REPO_ROOT / CI_WORKFLOW
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        raise SystemExit(f"ci_matrix: cannot read {CI_WORKFLOW}: {exc}")


def modules() -> list[str]:
    """The `-i` install list, in ci.yml's own order-insensitive set form."""
    return sorted(v for v in _ci_flag_values(_workflow_text(), "-i") if "/" not in v)


def tags() -> list[str]:
    """The `--test-tags` list, slashes preserved (odoo wants `/module`).

    Only `/`-prefixed values survive, and that filter is load-bearing rather than
    cosmetic. `_ci_flag_values` tokenises the whole workflow text, so PROSE counts:
    ci.yml contains the comment "`--test-tags` scopes the test run to OUR modules",
    and the tokeniser duly captured `scopes` as a value. That is harmless for
    `rule_ci_module_coverage`, which only ever asks "is module X in this set?" —
    but this function GENERATES a command line, where a bare `scopes` would be
    handed to odoo as a test tag. Requiring the leading slash keeps only real
    module scopes. Do not "simplify" this away.
    """
    return sorted(
        v for v in _ci_flag_values(_workflow_text(), "--test-tags") if v.startswith("/")
    )


def self_test() -> list[str]:
    """Return failures; empty means extraction still behaves.

    Guards the one failure mode that would otherwise be silent: ci.yml gets
    reformatted, the lists stop parsing, and `make test` cheerfully runs zero
    modules and reports success. An empty matrix must be loud, not green.
    """
    failures: list[str] = []
    mods, tgs = set(modules()), {t.lstrip("/") for t in tags()}

    if not mods:
        failures.append(
            "the -i list parsed as EMPTY. ci.yml likely changed shape; update "
            "_ci_flag_values in scripts/ci/invariants.py."
        )
    if not tgs:
        failures.append(
            "the --test-tags list parsed as EMPTY. ci.yml likely changed shape; "
            "update _ci_flag_values in scripts/ci/invariants.py."
        )
    if failures:
        return failures

    addons = REPO_ROOT / "custom_addons"
    declared = sorted(
        p.name for p in addons.iterdir()
        if p.is_dir() and (p / "__manifest__.py").is_file()
    ) if addons.is_dir() else []

    for module in declared:
        if module in CI_EXEMPT_MODULES:
            continue
        if module not in mods or module not in tgs:
            failures.append(
                f"`{module}` is in custom_addons but not in both ci.yml lists, so "
                f"`make test` would not run it. invariants.py should already have "
                f"caught this — run it."
            )
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Derive the test matrix from ci.yml")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--modules", action="store_true", help="print the -i list")
    group.add_argument("--tags", action="store_true", help="print the --test-tags list")
    group.add_argument("--self-test", action="store_true", help="verify extraction")
    args = parser.parse_args()

    if args.self_test:
        failures = self_test()
        if failures:
            print("ci_matrix self-test FAILED:", file=sys.stderr)
            for failure in failures:
                print(f"  - {failure}", file=sys.stderr)
            return 1
        print(
            f"ci_matrix self-test: ok "
            f"({len(modules())} module(s), {len(tags())} tag(s) from {CI_WORKFLOW})."
        )
        return 0

    print(",".join(modules() if args.modules else tags()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
