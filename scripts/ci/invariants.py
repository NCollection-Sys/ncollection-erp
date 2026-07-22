#!/usr/bin/env python3
"""Repo invariants guard — encodes the operational traps we have ACTUALLY hit.

Companion to architecture_guard.py. That one guards Odoo/addon architecture
(view syntax, two-layer separation, secrets). This one guards the *infrastructure*
surface — shell scripts, compose files, Makefile recipes — where our real
regressions have lived.

Every rule here exists because it would have caught a specific, diagnosed bug.
Rules that cannot be checked reliably are deliberately NOT implemented (see
"Deliberately not implemented" at the bottom) — a guard that cries wolf on
correct code is worse than no guard, because people learn to ignore it.

Usage:
    python scripts/ci/invariants.py            # scan the repo
    python scripts/ci/invariants.py --files a.sh b.yml
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Generated / vendored / transient trees are never ours to lint.
SKIP_PARTS = {"oca", "node_modules", ".git", "playwright-report", "test-results", ".oca-venv"}


# ---------------------------------------------------------------------------
# Known pending exceptions
# ---------------------------------------------------------------------------
# Real violations that are already diagnosed and scheduled, but whose fix is
# deliberately isolated into another PR. Each entry MUST name the follow-up, and
# the fixing PR MUST delete its entry (leaving it behind re-hides the bug).
KNOWN_PENDING: list[tuple[str, str, str]] = [
    # Empty, and it should stay that way. An entry here is a bug we have chosen to
    # ship — it must name its follow-up, and the fixing PR must delete its own entry
    # (leaving it behind re-hides the bug the guard exists to catch).
    # Last cleared: R-003, the pg_isready healthcheck (issue #157).
]


def is_pending(rel_path: str, line: str) -> bool:
    return any(
        rel_path == path and needle in line for path, needle, _reason in KNOWN_PENDING
    )


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

# R1 — Postgres CLI tools default the target database to the USERNAME when no
# -d is given. In this repo the role is "odoo" and no database "odoo" exists, so
# such a call always dies with `FATAL: database "odoo" does not exist`.
# Caught in the wild: verify_routing.sh:db_exists() always returned false, so the
# "idempotent" routing setup silently re-created every test DB on every run.
# NOTE: dropdb/createdb are intentionally NOT matched — they default their
# *maintenance* db to "postgres" and are correct without -d (verified live).
PG_TOOL_RE = re.compile(r"\b(psql|pg_isready)\b")
DB_FLAG_RE = re.compile(r"(?:^|\s)(?:-d|--dbname)(?:[=\s])")

# R2 — `|| true` on a state-changing docker command hides failure. Caught in the
# wild: setup_e2e_tenants.sh printed "✅ tenants ready" after a failed restart,
# leaving a stale @ormcache and producing baffling test results.
DOCKER_STATE_RE = re.compile(
    r"docker\s+(?:restart|start|stop)\b|docker\s+compose\s+(?:up|restart|start)\b"
)
SILENT_FAIL_RE = re.compile(r"\|\|\s*true")

# R3 — Hardcoded container names break under a non-default COMPOSE_PROJECT_NAME.
# Derive them instead: `docker compose ps -q <service>`.
HARDCODED_NAME_RE = re.compile(r"ncollection-(?:odoo|nginx|db|pgadmin)\b")


def rule_pg_explicit_db(rel: str, line: str, lineno: int, out: list[str]) -> None:
    if not PG_TOOL_RE.search(line) or DB_FLAG_RE.search(line):
        return
    out.append(
        f"{rel}:{lineno}: `psql`/`pg_isready` without an explicit -d. These default the "
        f"database to the USERNAME, which does not exist here -> always FATAL. "
        f"Add `-d postgres` (or the intended db).\n      {line.strip()}"
    )


def rule_no_silent_docker_failure(rel: str, line: str, lineno: int, out: list[str]) -> None:
    if DOCKER_STATE_RE.search(line) and SILENT_FAIL_RE.search(line):
        out.append(
            f"{rel}:{lineno}: `|| true` on a state-changing docker command hides failure. "
            f"If the step is load-bearing, fail loud with an actionable message.\n"
            f"      {line.strip()}"
        )


def rule_no_hardcoded_container(rel: str, line: str, lineno: int, out: list[str]) -> None:
    # `container_name:` in a compose file is the legitimate DEFINITION, not a usage.
    if "container_name:" in line:
        return
    if HARDCODED_NAME_RE.search(line):
        out.append(
            f"{rel}:{lineno}: hardcoded container name breaks a non-default "
            f"COMPOSE_PROJECT_NAME. Derive it: `docker compose ps -q <service>`.\n"
            f"      {line.strip()}"
        )


# ---------------------------------------------------------------------------
# File selection
# ---------------------------------------------------------------------------

def in_scope(path: Path) -> bool:
    return not any(part in SKIP_PARTS for part in path.parts)


def collect(explicit: list[str] | None) -> list[Path]:
    if explicit:
        return [Path(f) for f in explicit if Path(f).is_file()]
    found: list[Path] = []
    for pattern in ("*.sh", "docker-compose*.yml", "Makefile"):
        found.extend(REPO_ROOT.rglob(pattern))
    found.extend((REPO_ROOT / ".github" / "workflows").glob("*.yml"))
    # Git hooks are shell too, and carry no .sh extension — guard them as well.
    found.extend(p for p in (REPO_ROOT / ".githooks").glob("*") if p.is_file())
    return sorted({p for p in found if in_scope(p)})


def scan(path: Path, findings: list[str]) -> None:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return

    rel = str(path.relative_to(REPO_ROOT))
    is_makefile = path.name == "Makefile"
    is_workflow = ".github/workflows" in rel
    is_compose = path.name.startswith("docker-compose")
    # .githooks/* are shell scripts without a .sh extension.
    is_shell = path.suffix == ".sh" or rel.startswith(".githooks/")

    for lineno, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if is_pending(rel, line):
            continue

        # R1 — shell, compose and Makefile RECIPE lines only. Makefile recipes
        # start with a tab; scanning all lines would flag the `.PHONY: … psql …`
        # list and the `psql:` target definition, which invoke nothing.
        if is_shell or is_compose or (is_makefile and line.startswith("\t")):
            rule_pg_explicit_db(rel, line, lineno, findings)

        # R2 — shell scripts only.
        if is_shell:
            rule_no_silent_docker_failure(rel, line, lineno, findings)

        # R3 — shell scripts and workflow run-steps.
        if is_shell or is_workflow:
            rule_no_hardcoded_container(rel, line, lineno, findings)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files", nargs="*", help="explicit file list instead of a repo scan")
    args = parser.parse_args()

    paths = collect(args.files)
    findings: list[str] = []
    for path in paths:
        scan(path, findings)

    if findings:
        print("invariants: violations found\n")
        for f in findings:
            print(f"  ✗ {f}")
        print(f"\n{len(findings)} violation(s). Fix them, or — if genuinely blocked — add a")
        print("KNOWN_PENDING entry in scripts/ci/invariants.py naming the follow-up PR.")
        return 1

    print(f"invariants: clean ({len(paths)} file(s) scanned, 3 rule(s) applied).")
    if KNOWN_PENDING:
        print(f"  note: {len(KNOWN_PENDING)} known-pending exception(s) still registered:")
        for path, needle, reason in KNOWN_PENDING:
            print(f"    - {path}: {needle}  [{reason.split('—')[0].strip()}]")
    return 0


# ---------------------------------------------------------------------------
# Deliberately NOT implemented (and why) — revisit only with a reliable design.
#
# * Fixture-DB single-ownership (F3): the real invariant is "one suite owns a
#   fixture DB name". It cannot be checked statically because the drops are
#   parameterised (`drop_db "$1"`), so a static rule would be guesswork.
#   PR-3 removes the hazard structurally instead, by namespacing e2e fixtures.
#
# * workers>0 must pair with an nginx /websocket -> 8072 route: tempting, since
#   this exact mismatch cost hours in P1-T20. But docker-compose.saas.yml sets
#   --workers=2 on the provisioning-runner, a background queue_job container that
#   sits behind NO nginx and serves NO web client — the contract simply does not
#   apply to it. A naive rule flags correct code. Checking it properly needs
#   compose -> nginx topology modelling; until then the contract lives in
#   nginx/README.md and is asserted empirically by the e2e suite.
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    sys.exit(main())
