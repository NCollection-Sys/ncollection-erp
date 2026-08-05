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

# R4 — a module CI never installs is a module whose tests never run. Caught in
# the wild: ncollection_account_dashboard was in neither ci.yml's `-i` list nor
# its `--test-tags`, and nothing declares a manifest dependency on it, so from
# the day #119 created it until #329 every test it contained was inert. A real
# AttributeError on the CEO dashboard's happy path shipped through a green CI
# because of it (REGRESSIONS.md R-024).
#
# The failure mode is what makes it worth a guard: nothing turns red. A module
# with a full suite contributes ZERO enforcement, and the only signal is someone
# diffing ci.yml against `ls custom_addons/` by eye. It also recurs by default —
# every new module starts uncovered.
#
# Unlike R1-R3 this is a whole-repo set comparison, not a per-line pattern, so
# it runs once from main() rather than inside scan(). It deliberately runs even
# under --files: adding a module without touching ci.yml is exactly the change
# whose diff would NOT include ci.yml, so a diff-scoped version could never see
# the bug it exists to catch.
CI_WORKFLOW = ".github/workflows/ci.yml"

# Modules deliberately outside the CI matrix. Each entry MUST carry a reason —
# "excluded on purpose" and "forgotten" have to stay distinguishable, which is
# the entire point of the rule. A module listed here that IS covered is reported
# as a stale exemption, so the allowlist cannot quietly rot.
CI_EXEMPT_MODULES: dict[str, str] = {
    "ncollection_demo_freshorigin":
        "demo-tenant seeding, installed by `make demo-tenant`; not part of the CI matrix",
}

# Read the lists by TOKENISING the command the way bash does, not by matching a
# line. An earlier version anchored a regex to the physical line each flag starts
# (`^\s*-i\s+([A-Za-z0-9_,]+)`), which was wrong in a way that matters for a
# guard: ci.yml's `-i` and `--test-tags` lines are ~800 characters, so a future
# readability reflow splitting one at a comma —
#
#     --test-tags /ncollection_core,...,/ncollection_approvals,\
#     /ncollection_account_reports,...
#
# — is BYTE-IDENTICAL once bash joins the continuation, so odoo receives exactly
# the same argument and real coverage is untouched. The anchored regex, however,
# captured only the first physical line and would have reported four fully
# covered modules as missing. A guard that cries wolf on correct code is worse
# than no guard (see this module's docstring) — and false-alarming on a change
# that preserves the very property it checks is the worst version of that.
#
# Joining `\<newline>` with the empty string is precisely what bash does, so what
# we tokenise is what odoo actually receives. Tokenising also removes the old
# char-class fragility, where a `-` anywhere in a list (e.g. an Odoo negative
# test tag) silently truncated the match instead of failing loudly.
CI_LINE_CONTINUATION_RE = re.compile(r"\\\n")

# A module/tag list is word characters, commas, slashes and hyphens — nothing
# else. Anything containing a quote, brace or path separator is some other
# flag's operand and is discarded, so an unrelated `grep -i` cannot masquerade
# as the install list.
MODULE_LIST_RE = re.compile(r"[A-Za-z0-9_,/-]+")


def _ci_flag_values(text: str, flag: str, strip_slash: bool = False) -> set[str]:
    """Every comma-separated value passed to `flag` in the joined command text.

    Unrelated flags elsewhere in the workflow share these names — `grep -i` is
    already there today. Their operand is discarded by MODULE_LIST_RE rather
    than tolerated as harmless noise: an early version of this function let it
    through, reasoning that a stray token can never hide a real module. True for
    lookups, but it also kept `installed` non-empty when the real `-i` list had
    genuinely vanished, which defeated the "could not locate the lists" guard
    below and turned one clear finding into a report against every module — the
    exact avalanche that guard exists to prevent. Proven, not theorised: the
    reformatted-workflow scenario produced it.
    """
    tokens = CI_LINE_CONTINUATION_RE.sub("", text).split()
    values: set[str] = set()
    for token, following in zip(tokens, tokens[1:]):
        if token == flag and MODULE_LIST_RE.fullmatch(following):
            values |= {v for v in following.split(",") if v}
    return {v.lstrip("/") for v in values} if strip_slash else values


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


def rule_ci_module_coverage(out: list[str]) -> None:
    """Every custom_addons module must appear in ci.yml's -i AND --test-tags."""
    workflow = REPO_ROOT / CI_WORKFLOW
    try:
        text = workflow.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        out.append(
            f"{CI_WORKFLOW}: not readable, so CI module coverage cannot be verified."
        )
        return

    installed = _ci_flag_values(text, "-i")
    tagged = _ci_flag_values(text, "--test-tags", strip_slash=True)

    # One clear finding beats an avalanche of misleading ones: if the lists
    # cannot be located at all, every module would look uncovered and the real
    # problem (the workflow was reformatted) would be buried under the noise.
    #
    # This only defends TOTAL loss of a list. Partial loss used to be the real
    # hazard — a line-anchored regex capturing half a reflowed list — which is
    # why the lists are now tokenised from the joined command instead.
    if not installed or not tagged:
        out.append(
            f"{CI_WORKFLOW}: could not locate the `-i` and/or `--test-tags` lists, so "
            f"module coverage cannot be checked. If the odoo invocation changed shape, "
            f"update `_ci_flag_values` in scripts/ci/invariants.py."
        )
        return

    addons = REPO_ROOT / "custom_addons"
    modules = sorted(
        p.name for p in addons.iterdir()
        if p.is_dir() and (p / "__manifest__.py").is_file()
    ) if addons.is_dir() else []

    for module in modules:
        if module in CI_EXEMPT_MODULES:
            if module in installed and module in tagged:
                out.append(
                    f"{CI_WORKFLOW}: `{module}` is covered by CI but still listed in "
                    f"CI_EXEMPT_MODULES. Delete the stale exemption — an allowlist that "
                    f"outlives its reason hides the next real gap."
                )
            continue
        missing = [
            label for label, present in (("-i", module in installed),
                                         ("--test-tags", module in tagged))
            if not present
        ]
        if missing:
            out.append(
                f"{CI_WORKFLOW}: `{module}` is missing from {' and '.join(missing)}, so CI "
                f"never runs its tests — they are inert, and nothing turns red to say so. "
                f"Add it to both lists, or add it to CI_EXEMPT_MODULES with a reason."
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

    # R4 is repo-wide, so it runs regardless of --files. See its comment above:
    # the change that introduces the bug is precisely the one whose diff does
    # not include ci.yml.
    rule_ci_module_coverage(findings)

    if findings:
        print("invariants: violations found\n")
        for f in findings:
            print(f"  ✗ {f}")
        print(f"\n{len(findings)} violation(s). Fix them, or — if genuinely blocked — add a")
        print("KNOWN_PENDING entry in scripts/ci/invariants.py naming the follow-up PR.")
        return 1

    print(f"invariants: clean ({len(paths)} file(s) scanned, 4 rule(s) applied).")
    if CI_EXEMPT_MODULES:
        print(f"  note: {len(CI_EXEMPT_MODULES)} module(s) exempt from CI coverage:")
        for module, reason in CI_EXEMPT_MODULES.items():
            print(f"    - {module}  [{reason}]")
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
