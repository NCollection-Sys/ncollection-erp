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
#
# Matching a LITERAL `docker compose` was not enough, and the gap was not
# theoretical (#336). Neither of the two places this repo actually issues those
# commands spells them out:
#
#   Makefile      $(COMPOSE) up -d          $(ROUTING_COMPOSE) up -d
#   shell         "${DC[@]}" up -d          "${DCH[@]}" up -d --wait cron-stall-db
#
# so R2 was blind to every one of them. The variable forms below close that.
# They are deliberately general (any `$(…COMPOSE)` / `"${ARR[@]}"` followed by a
# state verb) rather than an allowlist of today's variable names, because the
# next script will invent a new one. A non-docker array happening to take a
# `start` argument is a tolerable false positive: R2 only fires when the line
# ALSO carries `|| true`, and the fix — don't swallow the failure — is right
# either way.
# `docker volume|network rm/prune` is included because destroying a fixture's
# storage is as state-changing as restarting it, and #335's bug was exactly
# that line with a `|| true` and an unconditional "✅ removed" after it.
# Bare `docker rm` is deliberately NOT here: removing a throwaway CONTAINER in a
# trap is legitimate best-effort cleanup nobody depends on, and this repo has
# six such lines today (3 in verify_cron_starvation.sh, 3 in pg_baseline.sh).
# Guarding it would cry wolf on correct code, which the module docstring rules out.
DOCKER_STATE_RE = re.compile(
    r"docker\s+(?:restart|start|stop)\b"
    r"|docker\s+(?:volume|network)\s+(?:rm|prune)\b"
    r"|docker\s+compose\s+(?:up|restart|start)\b"
    r"|\$\([A-Z_]*COMPOSE\)\s+(?:up|restart|start)\b"
    r"|\$\{[A-Za-z_]+\[@\]\}\"?\s+(?:up|restart|start)\b"
)
SILENT_FAIL_RE = re.compile(r"\|\|\s*true")

# `define NAME` / `define NAME =` opens a GNU make macro body, closed by `endef`.
# Body lines carry no leading tab, so they need explicit tracking to stay in
# scope — see the block comment in scan().
MAKE_DEFINE_RE = re.compile(r"define\s+[A-Za-z_][A-Za-z0-9_]*")

# R3 — Hardcoded container names break under a non-default COMPOSE_PROJECT_NAME.
# Derive them instead: `docker compose ps -q <service>`.
#
# Compose prefixes CONTAINERS, VOLUMES and NETWORKS with the same project name,
# so all three break the same way — but the rule only knew the four container
# names. #335 then shipped exactly that bug in the other shape:
#
#   docker volume rm -f ncollection-erp_cronstall_pgdata … >/dev/null 2>&1 || true
#
# hardcoded project prefix, swallowed failure, unconditional "✅ removed" after
# it. Under a non-default COMPOSE_PROJECT_NAME it would have cleaned nothing and
# reported success. A code reviewer caught it; this guard could not, on two
# counts — the pattern below, and the Makefile being out of scope entirely.
#
# `<project>_<name>` is the generic form, so match the project prefix itself
# rather than enumerating volumes. Derive instead:
#   docker compose config --format json | … ['name']   → the real project name
# The container names are DERIVED from the compose files, not enumerated here.
# The old hardcoded list named four services while the compose files defined
# eight — `pgbouncer`, `odoo-bus`, `certbot` and `provisioning-runner` were all
# invisible to R3, and any list would go stale again with the next service.
#
# A general `ncollection-<word>` pattern was tried first and REJECTED: it fired
# on `ghcr.io/ncollection-sys/…` (a GitHub org), `ncollection-sshd.local` and
# `52ncollection-unattended` (config filenames) — three false positives on
# correct code, which this module's docstring rules out. Matching only names a
# compose file actually declares as a container cannot make that mistake.
CONTAINER_NAME_RE = re.compile(r"container_name:\s*([A-Za-z0-9][A-Za-z0-9_.-]*)")

# Fallback if the compose files cannot be read. An empty derived set would make
# the container half of R3 silently match nothing — a guard that reports clean
# because it stopped looking is worse than one that is merely incomplete.
_FALLBACK_CONTAINERS = ("ncollection-odoo", "ncollection-nginx",
                        "ncollection-db", "ncollection-pgadmin")


def _declared_container_names() -> tuple[str, ...]:
    names: set[str] = set()
    for path in REPO_ROOT.glob("docker-compose*.yml"):
        try:
            names |= set(CONTAINER_NAME_RE.findall(path.read_text(encoding="utf-8")))
        except OSError:
            continue
    # Longest first so `ncollection-odoo-bus` wins over `ncollection-odoo`, and
    # the finding names the container that is actually written on the line.
    return tuple(sorted(names, key=len, reverse=True)) or _FALLBACK_CONTAINERS


# Order matters: the `<project>_<name>` alternative must come FIRST. Python's
# `|` takes the earliest alternative that matches at the leftmost position, and
# `erp_` does not satisfy `erp\b` (underscore is a word character), so without
# this ordering a volume name could match the container branch and get the wrong
# remediation advice.
HARDCODED_NAME_RE = re.compile(
    r"ncollection-erp_[A-Za-z0-9_]+"
    + "".join(f"|{re.escape(n)}\\b" for n in _declared_container_names())
)

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
    match = HARDCODED_NAME_RE.search(line)
    if match:
        # A `<project>_<name>` hit is a volume or network, not a container, so
        # `ps -q` is the wrong advice for it.
        if match.group(0).startswith("ncollection-erp_"):
            how = ("Derive the project name instead: `docker compose config "
                   "--format json` → `['name']`, then build `<project>_<name>`.")
            what = "volume/network name"
        else:
            how = "Derive it: `docker compose ps -q <service>`."
            what = "container name"
        out.append(
            f"{rel}:{lineno}: hardcoded {what} breaks a non-default "
            f"COMPOSE_PROJECT_NAME. {how}\n"
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
    # `*.mk` is here before any exists: the moment someone splits the Makefile,
    # the split half would otherwise leave R1-R3's scope silently (#336).
    for pattern in ("*.sh", "docker-compose*.yml", "Makefile", "*.mk"):
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
    is_makefile = path.name == "Makefile" or path.suffix == ".mk"
    is_workflow = ".github/workflows" in rel
    is_compose = path.name.startswith("docker-compose")
    # .githooks/* are shell scripts without a .sh extension.
    is_shell = path.suffix == ".sh" or rel.startswith(".githooks/")

    in_define = False

    for lineno, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if is_pending(rel, line):
            continue

        # `define … endef` macro bodies are NOT tab-indented, so a recipe-only
        # heuristic cannot see them — and `drop_database` is exactly such a
        # macro, invoked by every destructive `*-clean` target in the Makefile.
        # Without this, the #335 bug shape could be reintroduced inside the one
        # piece of Makefile infrastructure that backs all of them and no rule
        # would fire. Proven with a PoC before it was fixed.
        if is_makefile:
            if MAKE_DEFINE_RE.match(stripped):
                in_define = True
                continue        # the `define NAME` line itself invokes nothing
            if stripped == "endef":
                in_define = False
                continue

        # Makefile lines that actually RUN something: recipe lines (leading tab)
        # and macro bodies. Everything else — `.PHONY:` lists, variable
        # definitions, target headers — invokes nothing and would be noise.
        is_make_recipe = is_makefile and (line.startswith("\t") or in_define)

        # R1 — shell, compose, and Makefile recipe/macro lines.
        if is_shell or is_compose or is_make_recipe:
            rule_pg_explicit_db(rel, line, lineno, findings)

        # R2/R3 — shell, workflow run-steps, and Makefile recipe/macro lines
        # (#336). The Makefile used to be out of scope for both, which is
        # precisely where `make` issues its docker commands — and #335 shipped a
        # bug straight into that blind spot.
        if is_shell or is_make_recipe:
            rule_no_silent_docker_failure(rel, line, lineno, findings)

        if is_shell or is_workflow or is_make_recipe:
            rule_no_hardcoded_container(rel, line, lineno, findings)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------
# These regexes are the whole guard, and they are five alternations deep. They
# regress silently: a pattern that stops matching reports "clean", which is
# indistinguishable from a healthy repo. That is not hypothetical — while
# writing #336 a generalised `ncollection-<word>` container pattern was tried
# and fired on a GitHub org and two config filenames; it was caught only by
# running the tool by hand against the whole repo.
#
# So the cases run on EVERY invocation rather than living in a separate test
# nobody executes. They are pure regex matches — microseconds — and a failure
# here aborts before any scanning, because a broken guard must not be allowed
# to print a reassuring "clean".
SELF_TEST: tuple[tuple[str, str, bool], ...] = (
    # (regex name, line, should this line be flagged?)
    ("state", "docker restart odoo", True),
    ("state", "docker compose up -d", True),
    ("state", "\t$(COMPOSE) up -d", True),
    ("state", '\t"${DC[@]}" up -d --wait db', True),
    ("state", "docker volume rm -f x", True),
    ("state", "docker network prune -f", True),
    # Bare container removal is deliberately unguarded — see DOCKER_STATE_RE.
    ("state", "docker rm -f scratch", False),
    ("state", "docker compose ps -q db", False),
    ("state", "docker build -t img .", False),

    ("name", "docker volume rm ncollection-erp_cronstall_pgdata", True),
    ("name", "docker logs ncollection-odoo-bus", True),
    ("name", "docker exec ncollection-odoo bash", True),
    # The three false positives a generalised pattern produced (#336).
    ("name", "docker build -t ghcr.io/ncollection-sys/ncollection-erp:local .", False),
    ("name", "install -m 0644 /etc/fail2ban/jail.d/ncollection-sshd.local", False),
    ("name", "install -m 0644 /etc/apt/apt.conf.d/52ncollection-unattended", False),
    ("name", "cd /srv/ncollection-erp/scripts && ./run.sh", False),

    ("pg", "psql -U odoo -c 'SELECT 1'", True),
    ("pg", "psql -U odoo -d postgres -c 'SELECT 1'", False),
    ("pg", "pg_isready -U odoo -d postgres", False),
    ("pg", "dropdb -U odoo --if-exists scratch", False),
)


def run_self_test() -> list[str]:
    """Return a list of failures; empty means the patterns still behave."""
    failures: list[str] = []
    for name, line, expected in SELF_TEST:
        if name == "state":
            actual = bool(DOCKER_STATE_RE.search(line))
        elif name == "name":
            actual = bool(HARDCODED_NAME_RE.search(line))
        else:
            actual = bool(PG_TOOL_RE.search(line)) and not DB_FLAG_RE.search(line)
        if actual is not expected:
            failures.append(
                f"{name}: expected {'a hit' if expected else 'no hit'} on {line!r}"
            )
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files", nargs="*", help="explicit file list instead of a repo scan")
    args = parser.parse_args()

    # Before anything else: a guard that cannot detect its own breakage would
    # otherwise print "clean" over a repo it is no longer really checking.
    broken = run_self_test()
    if broken:
        print("invariants: SELF-TEST FAILED — the guard itself is broken\n")
        for f in broken:
            print(f"  ✗ {f}")
        print("\nThe patterns in scripts/ci/invariants.py no longer behave as documented.")
        print("Fix them before trusting any scan result.")
        return 1

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
