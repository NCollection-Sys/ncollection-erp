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
import functools
import io
import re
import tokenize
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


def _declared_container_names(root: Path) -> tuple[str, ...]:
    names: set[str] = set()
    for path in root.glob("docker-compose*.yml"):
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
# Built LAZILY, per repo root, not once at import (#330). A module-level
# constant froze the derived names at import time, which made every test that
# pointed REPO_ROOT at a fixture silently exercise THIS repo's real names
# instead — a test that could not fail, which is precisely what the harness
# exists to prevent. Cached because the rule is called per LINE and globbing
# compose files each time would be O(lines x files).
@functools.lru_cache(maxsize=8)
def _hardcoded_name_re(root: Path) -> re.Pattern:
    return re.compile(
        r"ncollection-erp_[A-Za-z0-9_]+"
        + "".join(f"|{re.escape(n)}\\b"
                  for n in _declared_container_names(root))
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

# R5 — the dev and routing Odoo containers must keep `--max-cron-threads`.
#
# Without it, Odoo's cron worker enumerates EVERY database on the server:
# `cron_database_list()` is `config['db_name'] or list_dbs(True)`, and neither
# container passes `-d`. `--db-filter` does NOT restrict this — it filters HTTP
# routing only, which is exactly the trap that let the problem live unnoticed
# (#337). Measured before the fix: 32 cron jobs in 3 minutes across 21
# databases, none of them the working one.
#
# Why this is worth a rule rather than the comments #337 shipped with:
#
#   * It is SILENT. Crons ticking 89 databases produce no error and no failing
#     suite — only log noise nobody reads.
#   * It CORRUPTS MEASUREMENT. It is how #310's first harness "proved" 46s of
#     cron starvation that never happened, by attributing the shared
#     container's own ~60s cron cycle to its harness. That cost a full rebuild
#     of the harness (DESIGN_CRON_AND_QUEUE_TOPOLOGY.md §5.1).
#
# The VALUE is checked, not merely the flag's presence. An earlier version
# checked only that the string appeared, reasoning that policing the value would
# break the `${NC_DEV_CRON_THREADS:-0}` escape hatch. That was confused, and
# review reproduced two false negatives against it:
#
#   1. `command: odoo …   # was: --max-cron-threads=0, see #337`
#      The flag is GONE from the invoked command, but the substring survives in
#      a trailing comment -> reported clean, on a live regression. Commenting a
#      flag out "for now" is the single most likely way this comes back.
#
#   2. `--max-cron-threads=64`
#      Passes as compliant while fully re-enabling the bug. Per this rule's own
#      mechanics above, ANY non-zero thread count still runs
#      `cron_database_list()` -> `list_dbs(True)` without a `-d`, so it ticks
#      every database — just with a concurrency cap. Only 0 spawns no cron
#      thread at all.
#
# The hatch survives because it lives at RUNTIME, not in the file: the committed
# default is `${NC_DEV_CRON_THREADS:-0}`, and a developer opts in with
# `NC_DEV_CRON_THREADS=1 make up` without editing anything the guard reads.
#
# Whole-file, not per-line, because absence is the thing being detected — a
# line scanner can only see lines that exist.
CRON_SCOPED_FILES = ("docker-compose.dev.yml", "docker-compose.routing.yml")
CRON_THREADS_FLAG = "--max-cron-threads"
ODOO_COMMAND_RE = re.compile(r"^\s*command:.*\bodoo\b")

# The flag's value, stopping at whatever quotes/brackets the YAML form uses, so
# both `--max-cron-threads=0` (list form, quoted) and the shell-string form are
# captured cleanly.
CRON_THREADS_VALUE_RE = re.compile(r"--max-cron-threads=([^\s\"',\]]+)")

# Accepted values: a literal 0, or an interpolation whose DEFAULT is 0.
CRON_THREADS_OK_RE = re.compile(r"^(?:0|\$\{[A-Za-z_][A-Za-z0-9_]*:-0\})$")


# ---------------------------------------------------------------------------
# R6 — an Odoo container that RUNS cron must say WHICH database's cron it runs.
#
# R5 above is the "must not run cron at all" rule, and it covers exactly two
# files. This is the other half, and it covers every compose file: if a service
# runs cron (threads != 0), it must carry `-d`, or be listed below as a
# deliberate enumerator.
#
# WHY IT IS A SEPARATE RULE RATHER THAN MORE FILES IN R5. #337 fixed dev and
# routing with `--max-cron-threads=0`, because those must not run cron. Applying
# that to `provisioning-runner` would have SILENTLY STOPPED the config-sync
# reconcile job that lives there (#343). Same bug, opposite fix — so the guard
# has to distinguish "must not run cron" from "must run only its own cron",
# which one rule cannot.
#
# WHY THE BUG SURVIVED R5. R5's matcher is `^\s*command:.*\bodoo\b`, and
# `docker-compose.saas.yml` writes its command as a folded block:
#
#     command: >
#       odoo -c /etc/odoo/odoo.conf
#            --max-cron-threads=1
#
# so the flags live on continuation lines and the matcher sees NOTHING there —
# measured: 0 matching lines in that file. R6 therefore gathers the whole block
# before looking at it. A line-shaped rule cannot guard a block-shaped setting.
#
# ABSENT IS NOT ZERO. A command with no `--max-cron-threads` inherits the
# config file's value, and `config/odoo.prod.conf` sets `max_cron_threads = 1`.
# So "no flag" means "runs cron", which is why the check below treats a missing
# flag as cron-enabled rather than skipping the service.
#
# THE ALLOWLIST IS THE POINT. Enumerating every database is CORRECT for the
# production cron worker — that is how one Odoo runs every tenant's crons. The
# rule does not forbid it; it forbids doing it by accident. Adding an entry is
# a recorded decision with a reason, which is what #343's acceptance criteria
# asked for and what a bare code change would not have produced.
# Reported in the clean line. A literal here drifts the moment a rule is
# added — it already read `5` with six rules wired.
RULE_COUNT = 9

CRON_ENUMERATORS_ALLOWED: dict[tuple[str, str], str] = {
    ("docker-compose.pooling.yml", "odoo-bus"):
        "THE tenant cron worker in the pooling split (P2-T09). Running every "
        "database's crons is its entire job; scoping it to one would stop cron "
        "for every tenant.",
    ("docker-compose.prod.yml", "odoo"):
        "The single-container production shape, used when the pooling overlay "
        "is NOT layered. It is then the only cron worker, so it must enumerate "
        "for the same reason odoo-bus does.",
    ("docker-compose.staging.yml", "odoo"):
        "Same shape and same reason as prod: staging mirrors production, and a "
        "staging that skipped tenant crons would not be a rehearsal of it.",
}
# A service with NO `command:` at all runs the image's CMD, and `odoo:19`'s is a
# bare `odoo` whose built-in defaults are max_cron_threads=2 and db_name unset —
# i.e. the #337/#343 disease with no line for a line-based rule to read. That is
# how the BASE docker-compose.yml's own `odoo` service stayed invisible to R5 and
# R6 alike while `.github/workflows/ci.yml`'s build job ran `docker compose up -d`
# against exactly that shape. Found by review, not by the rule that was written
# to prevent it.
#
# These are the services allowed to carry no command, with the reason:
CRON_COMMANDLESS_ALLOWED: dict[tuple[str, str], str] = {
    ("docker-compose.cronscope.yml", "cron-scope-runner"):
        "Harness service, never started by `up`. Every invocation is a "
        "`compose run` from verify_cron_scope.sh supplying its own command — "
        "the two arms differ by one flag, so a command here would hide the "
        "variable under test.",
    ("docker-compose.cronstall.yml", "cron-stall-odoo"):
        "Same shape, same reason (#310): the starvation harness supplies one "
        "command per arm.",
}
COMPOSE_IMAGE_ODOO_RE = re.compile(r"^\s+image:\s*\S*odoo", re.MULTILINE)
COMPOSE_HAS_CMD_RE = re.compile(r"^\s+(?:command|entrypoint):", re.MULTILINE)

# KNOWN BLIND SPOTS, stated rather than discovered later (both confirmed absent
# from this repo today, both found by the review of this rule):
#   * a command supplied through a YAML anchor/merge key (`x-base: &base` +
#     `<<: *base`) lives outside the `services:` block this scans, so the
#     service reads as commandless — it would be flagged, not missed, which is
#     the safe direction, but the message would be wrong.
#   * `entrypoint: ["odoo"]` with the flags in a separate `command:` is treated
#     as "has a command" and then only matches if the text happens to contain
#     `odoo` — for a differently-named config path it would be dropped.
# Closing either properly needs a YAML parser; invariants.py deliberately has no
# third-party imports, so this is documented instead of half-done.
COMPOSE_SERVICE_RE = re.compile(r"^  ([A-Za-z0-9_.-]+):\s*$")
COMPOSE_TOPLEVEL_RE = re.compile(r"^([A-Za-z0-9_.-]+):")
COMPOSE_COMMAND_RE = re.compile(r"^(\s+)command:\s*(.*)$")
# `-d <db>` / `-d=<db>` / `--database <db>`. The lookbehind keeps `--db-filter`
# from matching: its `-d` is preceded by a `-`.
# `-d db` / `-d=db` / `--database db`, and the JSON-list form where the flag is
# its own quoted token (`["odoo", "-d", "somedb"]`) and is followed by a quote or
# comma rather than a space. Missing that spelling made the rule cry wolf on a
# compliant command. The lookbehind keeps `--db-filter` from matching: its `-d`
# is preceded by a `-`.
DB_SCOPE_RE = re.compile(r"(?<![\w-])(?:-d|--database)(?=[ =\"',\]]|$)")


def _compose_odoo_commands(text: str) -> list[tuple[str, str, int]]:
    """Every Odoo `command:` in a compose file, as (service, command, lineno).

    Folded (`>`) and literal (`|`) blocks are joined, so the flags on their
    continuation lines are visible. Only keys inside `services:` are treated as
    service names — `networks:` and `volumes:` use the same indentation.
    """
    out: list[tuple[str, str, int]] = []
    lines = text.splitlines()
    service, in_services, i = None, False, 0
    while i < len(lines):
        line = lines[i]
        top = COMPOSE_TOPLEVEL_RE.match(line)
        if top:
            in_services = top.group(1) == "services"
            service = None
            i += 1
            continue
        svc = COMPOSE_SERVICE_RE.match(line)
        if svc and in_services:
            service = svc.group(1)
            i += 1
            continue
        cmd_m = COMPOSE_COMMAND_RE.match(line)
        if cmd_m and service and not line.strip().startswith("#"):
            indent, rest = cmd_m.group(1), cmd_m.group(2).strip()
            if rest in (">", "|", ">-", "|-", ">+", "|+"):
                body, j = [], i + 1
                while j < len(lines):
                    nxt = lines[j]
                    if nxt.strip() and not nxt.startswith(indent + " "):
                        break
                    body.append(_strip_yaml_comment(nxt).strip())
                    j += 1
                out.append((service, " ".join(body), i + 1))
                i = j
                continue
            out.append((service, _strip_yaml_comment(rest), i + 1))
        i += 1
    return [(s, c, n) for s, c, n in out if re.search(r"\bodoo\b", c)]


def _commandless_odoo_services(text: str) -> list[tuple[str, int]]:
    """Odoo-image services with no `command:`/`entrypoint:` of their own."""
    found: list[tuple[str, int]] = []
    lines = text.splitlines()
    service, start, in_services, body = None, 0, False, []

    def flush() -> None:
        if service and COMPOSE_IMAGE_ODOO_RE.search("\n".join(body)) \
                and not COMPOSE_HAS_CMD_RE.search("\n".join(body)):
            found.append((service, start))

    for i, line in enumerate(lines, 1):
        top = COMPOSE_TOPLEVEL_RE.match(line)
        if top:
            flush()
            service, body = None, []
            in_services = top.group(1) == "services"
            continue
        svc = COMPOSE_SERVICE_RE.match(line)
        if svc and in_services:
            flush()
            service, start, body = svc.group(1), i, []
            continue
        if service:
            body.append(line)
    flush()
    return found


# ---------------------------------------------------------------------------
# R8 — an anchored validator must be applied with .fullmatch(), never .match().
#
# `$` does NOT mean end-of-string in Python. It matches at the end OR
# immediately before a trailing newline. So `re.compile(r'^[a-z]+$').match(
# "postgres\n")` returns a match, and every `^...$` + `.match()` validator in
# the repo silently accepted a trailing newline — measured, 11 of them.
#
# WHY THAT IS WORSE THAN A STRAY CHARACTER. These validators are followed by
# EXACT comparisons that then miss:
#
#     if not DB_NAME_RE.match(db): raise ...        # "postgres\n" passes
#     if db in RESERVED_DB_NAMES or db == cr.dbname: raise ...   # and MISSES
#
# so the newline defeats the reserved-name and self-target guards, which are
# the actual security control. Same shape in checkout.py for reserved
# subdomains. No live exploit was found (the subdomain path is saved by an
# upstream .strip(), and a DROP uses sql.Identifier so "postgres\n" is a
# distinct identifier) — but the protection is INCIDENTAL, and a validator
# whose correctness depends on some caller remembering to normalise is not a
# validator.
#
# Three anchor bugs shipped in one week before this rule existed (#377): a
# trailing `\b` that let `card 4111111111111111_2026` reach a third-party LLM
# unredacted, the identical bug one level down in its own guard, and this `$`
# one. Every one was found by a human reading code.
#
# Scoped to `.match(` on a name whose pattern is anchored, so a deliberate
# prefix match on an UNanchored pattern is untouched.
#
# KNOWN BLIND SPOTS, enumerated by the review that found the cross-module one.
# None exist in the tree today (checked: no `rb'^`, no `re.VERBOSE`, no
# `re.compile(a + b)` under VALIDATOR_DIRS), so these are robustness debt
# rather than live gaps — written down so the next reader does not have to
# rediscover them:
#   * byte patterns `re.compile(rb'^...$')` — the `r?` prefix does not allow
#     `rb`.
#   * a pattern built by concatenation, `re.compile(r'^' + SUFFIX)`.
#   * a triple-quoted multi-line `re.VERBOSE` pattern — the capture has no
#     re.DOTALL, so it cannot span a newline.
#   * the literal text `NAME.match(` inside a string or a TRAILING comment is
#     read as a call site; only whole comment LINES are skipped.
ANCHORED_COMPILE_RE = re.compile(
    r"(?P<name>\w+)\s*=\s*re\.compile\(\s*r?[\'\"](?P<pat>\^.*\$)[\'\"]",
    re.MULTILINE)
# The trees whose validators this rule polices. VERIFIED TO EXIST at run time
# below — the first version named "ai_gateway/", which is not a directory (the
# real path is satellites/ai_gateway/), so the rule scanned ZERO gateway files
# while its own docstring claimed to cover them. It reported clean either way.
# A guard pointed at a path that does not exist is worse than no guard, so a
# missing entry is now a hard failure rather than silent nil coverage.
VALIDATOR_DIRS = ("custom_addons/", "satellites/")


# Any `NAME = re.compile(...)` — used to spot a name compiled BOTH anchored and
# unanchored somewhere in the tree, which would make a global judgement unsafe.
ANY_COMPILE_RE = re.compile(r"(?P<name>\w+)\s*=\s*re\.compile\(", re.MULTILINE)


def _match_call_lines(text: str, names: set[str]) -> list[tuple[int, str]]:
    """Line numbers where `NAME.match(` appears as CODE, for NAME in `names`.

    Tokenised rather than grepped. This codebase documents old bugs by QUOTING
    the buggy line in prose — this rule's own comment block does it, and so
    does test_regex_anchors.py — so a text scan flags the documentation and
    fails the build over an explanation. It also missed the reverse case: a
    `.match(` in a trailing comment was treated as a call, because only whole
    comment LINES were skipped. tokenize sees neither strings nor comments,
    which removes both.
    """
    hits = []
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        # Unparseable source is someone else's failure (flake8, the test run);
        # reporting nothing here is right, but never report CLEAN silently.
        return hits
    for i, tok in enumerate(toks[:-3]):
        if tok.type != tokenize.NAME or tok.string not in names:
            continue
        dot, meth, par = toks[i + 1], toks[i + 2], toks[i + 3]
        if (dot.type == tokenize.OP and dot.string == "."
                and meth.type == tokenize.NAME and meth.string == "match"
                and par.type == tokenize.OP and par.string == "("):
            hits.append((tok.start[0], tok.string))
    return hits


def _validator_sources() -> list[tuple[str, str]]:
    """(relative path, text) for every non-test module under VALIDATOR_DIRS."""
    out = []
    for path in sorted(REPO_ROOT.rglob("*.py")):
        rel = str(path.relative_to(REPO_ROOT))
        if not rel.startswith(VALIDATOR_DIRS) or "/tests/" in rel:
            continue
        try:
            out.append((rel, path.read_text(encoding="utf-8", errors="ignore")))
        except OSError:
            continue
    return out


def rule_anchored_regex_uses_fullmatch(out: list[str]) -> None:
    """`^...$` compiled here, `.match()` called THERE — flag it (#377).

    REPO-WIDE, not per file, and that is the whole point of the second pass.
    The first version collected anchored names from each file's own text, so a
    pattern compiled in one module and IMPORTED into another was invisible —
    including `DB_NAME_RE`, which `saas_subprocess.py` defines and
    `provisioning_job.py` imports and uses. That call site is the one this
    rule's own rationale quotes as the canonical vulnerable shape, and the rule
    could not see it: reverting it to `.match()` reported clean. Found by
    review, on the commit that added the rule.

    A name compiled BOTH anchored and unanchored anywhere in the tree is
    ambiguous — `.match()` on it may be perfectly correct — so it is skipped
    rather than guessed at. None exist today; the check is here so that adding
    one does not silently turn this rule into a source of false positives,
    which is how a guard trains people to ignore it.
    """
    missing = [d for d in VALIDATOR_DIRS if not (REPO_ROOT / d).is_dir()]
    if missing:
        out.append(
            f"invariants R8: VALIDATOR_DIRS names {missing}, which do not "
            f"exist — the rule would scan nothing there and still report "
            f"clean. Fix the path or drop the entry (#377).")
        return
    sources = _validator_sources()
    anchored, every = set(), set()
    for _rel, text in sources:
        anchored |= {m.group("name") for m in ANCHORED_COMPILE_RE.finditer(text)}
        every |= {m.group("name") for m in ANY_COMPILE_RE.finditer(text)}
    # compiled somewhere without the ^...$ shape as well -> cannot judge
    ambiguous = {
        n for n in anchored
        if sum(1 for _r, t in sources
               for m in ANY_COMPILE_RE.finditer(t) if m.group("name") == n)
        > sum(1 for _r, t in sources
              for m in ANCHORED_COMPILE_RE.finditer(t) if m.group("name") == n)
    }
    checked = anchored - ambiguous
    if not checked:
        return
    for rel, text in sources:
        for lineno, name in _match_call_lines(text, checked):
            out.append(
                f"{rel}:{lineno}: `{name}` is anchored (^...$) but applied "
                f"with .match() — `$` also matches before a trailing "
                f"newline, so \"value\\n\" passes and any exact "
                f"comparison after it (reserved names, self-target) "
                f"MISSES. Use .fullmatch() (#377).")


def rule_cron_enumeration_declared(out: list[str]) -> None:
    """A cron-running Odoo service must scope cron, or be an allowed enumerator."""
    for path in sorted(REPO_ROOT.glob("docker-compose*.yml")):
        rel = path.name
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            out.append(f"{rel}: not readable, so the cron-enumeration guard "
                       f"cannot run.")
            continue
        for service, lineno in _commandless_odoo_services(text):
            if (rel, service) in CRON_COMMANDLESS_ALLOWED:
                continue
            out.append(
                f"{rel}:{lineno}: service `{service}` uses an Odoo image with no "
                f"`command:`, so it runs the image default — `max_cron_threads=2` "
                f"and no `-d`, which ticks EVERY database on the server "
                f"(#337/#343). Give it an explicit command, or add "
                f"('{rel}', '{service}') to CRON_COMMANDLESS_ALLOWED in "
                f"scripts/ci/invariants.py with the reason it needs none."
            )
        for service, command, lineno in _compose_odoo_commands(text):
            value_m = CRON_THREADS_VALUE_RE.search(command)
            if value_m and CRON_THREADS_OK_RE.match(value_m.group(1)):
                continue                      # cron disabled — R5's territory
            if DB_SCOPE_RE.search(command):
                continue                      # scoped to one database
            if (rel, service) in CRON_ENUMERATORS_ALLOWED:
                continue                      # deliberate, with a reason
            threads = value_m.group(1) if value_m else "unset (inherits the conf)"
            out.append(
                f"{rel}:{lineno}: service `{service}` runs cron "
                f"(--max-cron-threads={threads}) with no `-d`, so "
                f"`cron_database_list()` falls back to `list_dbs(True)` and it "
                f"ticks EVERY database on the server (#337/#343). `--db-filter` "
                f"does NOT restrict cron — it filters HTTP routing only. Either "
                f"add `-d <db>`, or add ('{rel}', '{service}') to "
                f"CRON_ENUMERATORS_ALLOWED in scripts/ci/invariants.py with the "
                f"reason it must enumerate."
            )


def _strip_yaml_comment(line: str) -> str:
    """Drop a trailing ` # …` comment.

    Without this the guard can be defeated by commenting the flag out and
    leaving the old text on the line — see case 1 above. A `#` only opens a
    comment when preceded by whitespace, which is what distinguishes it from a
    `#` inside a value.
    """
    index = line.find(" #")
    return line if index == -1 else line[:index]


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
# — is byte-identical once bash joins the continuation ONLY IF the continuation
# is UNINDENTED. Bash removes `\<newline>` and nothing else, so:
#
#     -i mod_a,\<nl>mod_b            -> ['-i', 'mod_a,mod_b']     identical
#     -i mod_a,\<nl>    mod_b        -> ['-i', 'mod_a,', 'mod_b'] NOT identical
#
# (verified against real bash, #330). The indented form genuinely drops mod_b
# from the install list, so flagging it is CORRECT — and it is the form a human
# reflowing a long line inside a YAML block scalar would naturally write. Both
# cases are pinned in scripts/ci/test_invariants.py. The anchored regex, however,
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
    match = _hardcoded_name_re(REPO_ROOT).search(line)
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


def rule_cron_threads_scoped(out: list[str]) -> None:
    """Every dev/routing Odoo `command:` must still carry --max-cron-threads.

    See the R5 block above for why. Two failure modes are reported separately
    on purpose:

    * the flag is gone      -> the regression is back;
    * the command is gone   -> this rule can no longer see what it guards, and
                               saying so loudly beats reporting "clean" over a
                               file it no longer understands. That is the same
                               choice R4 makes when it cannot locate ci.yml's
                               lists, and for the same reason.
    """
    for name in CRON_SCOPED_FILES:
        path = REPO_ROOT / name
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            out.append(
                f"{name}: not readable, so the cron-scoping guard cannot run. "
                f"If the file was renamed, update CRON_SCOPED_FILES in "
                f"scripts/ci/invariants.py."
            )
            continue

        commands = [
            line for line in text.splitlines()
            if ODOO_COMMAND_RE.match(line) and not line.strip().startswith("#")
        ]
        if not commands:
            out.append(
                f"{name}: could not find the odoo service's `command:` line, so "
                f"it cannot be checked for `{CRON_THREADS_FLAG}`. If the command "
                f"moved to a multi-line form, update ODOO_COMMAND_RE in "
                f"scripts/ci/invariants.py rather than deleting this rule."
            )
            continue

        # EVERY matched command must carry it, not merely one of them. With one
        # command per scoped file today the distinction is invisible — but
        # docker-compose.pooling.yml has two Odoo services with different thread
        # counts, so whoever extends this rule for #343 would otherwise inherit
        # a check that passes a file while one of its services lost the flag.
        for line in commands:
            values = CRON_THREADS_VALUE_RE.findall(_strip_yaml_comment(line))
            if not values:
                out.append(
                    f"{name}: the odoo `command:` no longer passes "
                    f"`{CRON_THREADS_FLAG}` (#337). Without it this container "
                    f"runs the crons of EVERY database on the server — "
                    f"`db_filter` does NOT restrict cron, only HTTP routing. It "
                    f"is silent, and it corrupts any timing measured against a "
                    f"shared-server database (see "
                    f"DESIGN_CRON_AND_QUEUE_TOPOLOGY.md §5.1).\n"
                    f"      {line.strip()}"
                )
                continue

            for value in values:
                if CRON_THREADS_OK_RE.match(value):
                    continue
                out.append(
                    f"{name}: `{CRON_THREADS_FLAG}={value}` does not scope cron "
                    f"(#337). Any NON-ZERO thread count still enumerates every "
                    f"database — `cron_database_list()` is "
                    f"`config['db_name'] or list_dbs(True)` and there is no "
                    f"`-d` here, so a cap on concurrency is not a cap on which "
                    f"databases tick. Use `0`, or an interpolation defaulting "
                    f"to 0 such as `${{NC_DEV_CRON_THREADS:-0}}`.\n"
                    f"      {line.strip()}"
                )


# --- R9: workflow actions must be SHA-pinned ------------------------------
# A `uses: owner/action@v4` reference resolves a MUTABLE tag: whoever controls
# that repo can move it, and the next CI run executes different code with our
# repository token. Every workflow here pinned a full SHA except one — canary.yml
# used `actions/checkout@v4` — and nothing noticed, because nothing looked.
#
# Found while bumping the actions off deprecated Node 20. Fixing the one instance
# without this rule would leave the class open: the next hand-written step is
# just as likely to paste a tag.
WORKFLOW_DIR = ".github/workflows"
# A local reusable workflow (`uses: ./.github/...`) is our own file at our own
# commit — there is no third party and no tag to move.
_USES_RE = re.compile(r"^\s*-?\s*uses:\s*(?P<ref>\S+)", re.MULTILINE)
_SHA_PINNED_RE = re.compile(r"^[\w.-]+/[\w.-]+(?:/[\w.-]+)*@[0-9a-f]{40}$")


def rule_workflow_actions_sha_pinned(out: list[str]) -> None:
    """Every third-party `uses:` in .github/workflows must pin a 40-char SHA."""
    wf_dir = REPO_ROOT / WORKFLOW_DIR
    if not wf_dir.is_dir():
        out.append(
            f"{WORKFLOW_DIR}: directory not found, so the action-pinning guard "
            f"cannot run. A guard aimed at nothing must not report clean."
        )
        return
    files = sorted(wf_dir.glob("*.yml")) + sorted(wf_dir.glob("*.yaml"))
    if not files:
        out.append(
            f"{WORKFLOW_DIR}: no workflow files found, so this rule verified "
            f"nothing. If workflows moved, update WORKFLOW_DIR."
        )
        return
    for path in files:
        rel = path.relative_to(REPO_ROOT).as_posix()
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            out.append(f"{rel}: not readable, so its actions cannot be checked.")
            continue
        for m in _USES_RE.finditer(text):
            ref = m.group("ref").strip().strip('"\'')
            if ref.startswith("./") or ref.startswith("."):
                continue                      # our own reusable workflow
            if _SHA_PINNED_RE.match(ref):
                continue
            lineno = text[: m.start()].count("\n") + 1
            out.append(
                f"{rel}:{lineno}: `uses: {ref}` is not pinned to a 40-character "
                f"commit SHA. A tag or branch is MUTABLE — whoever owns that "
                f"repository can move it and run different code with this "
                f"repo's token on the next CI run. Pin the SHA and keep the "
                f"version in a trailing comment."
            )


def rule_ai_gateway_kdf_agrees(out: list[str]) -> None:
    """The AI-gateway key derivation exists TWICE and must AGREE BY OUTPUT (#373).

    `satellites/ai_gateway/tenant_auth.py` verifies the signature;
    `custom_addons/ncollection_ai/models/gateway_client.py` produces it. Neither
    can import the other: the satellite is plain Python with no Odoo, and the
    addon must not import `satellites/` (enforced by
    ncollection_ai/tests/test_isolation.py::
    test_the_addon_never_imports_from_the_satellite). So the copy is deliberate.

    THIS RULE COMPARES BEHAVIOUR, NOT TEXT, and that distinction is the whole
    point. The first version matched three literal substrings, and a reviewer
    showed it was blind to the single most important line: swapping
    `KDF_LABEL + tenant` for `tenant + KDF_LABEL` in one file produces a
    completely different signature while every substring is still present and
    every unit test on both sides still passes (each recomputes its expectation
    with the same function it is testing, so they are self-referential).

    So both implementations are executed on fixed vectors and their outputs
    compared. Any divergence — order, encoding, digest, delimiter — fails here.
    The functions are extracted with `ast` and exec'd in an empty namespace, so
    neither file is imported: `gateway_client.py` imports odoo and would not
    load in CI.
    """
    import ast

    def _extract(rel: str, wanted: dict[str, str]) -> dict | None:
        """exec the named top-level functions/constants, without importing."""
        path = REPO_ROOT / rel
        try:
            source = path.read_text(encoding="utf-8")
        except OSError:
            out.append(f"{rel}: unreadable, so the AI-gateway key derivation "
                       f"cannot be compared with its counterpart (#373).")
            return None
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            out.append(f"{rel}: does not parse ({exc}), so the AI-gateway key "
                       f"derivation cannot be compared (#373).")
            return None

        keep: list[ast.stmt] = []
        names = set(wanted.values())
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name in names:
                keep.append(node)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in names:
                        keep.append(node)
        # ONE namespace for globals and locals: a function body resolves names
        # against its GLOBALS, so splitting them leaves KDF_LABEL invisible to
        # the very function that uses it.
        namespace: dict = {"hmac": __import__("hmac"),
                           "hashlib": __import__("hashlib")}
        exec(compile(ast.Module(body=keep, type_ignores=[]), rel, "exec"),
             namespace)
        missing = [n for n in wanted.values() if n not in namespace]
        if missing:
            out.append(
                f"{rel}: {', '.join(sorted(missing))} not found at module level, "
                f"so the AI-gateway signing scheme cannot be compared with its "
                f"counterpart (#373). If it was renamed, update this rule — do "
                f"not leave the two sides unchecked."
            )
            return None
        return {alias: namespace[real] for alias, real in wanted.items()}

    satellite = _extract(
        "satellites/ai_gateway/tenant_auth.py",
        {"derive": "derive_tenant_key", "sign": "sign", "label": "KDF_LABEL"},
    )
    addon = _extract(
        "custom_addons/ncollection_ai/models/gateway_client.py",
        {"derive": "_derive_tenant_key", "sign": "_sign", "label": "_KDF_LABEL"},
    )
    if not satellite or not addon:
        return

    if satellite["label"] != addon["label"]:
        out.append(
            f"AI-gateway domain-separation labels differ: "
            f"{satellite['label']!r} vs {addon['label']!r} (#373)."
        )

    # Fixed vectors, including a tenant containing the label itself and one
    # with an underscore, since db names may carry them.
    for master, tenant, stamp, body in (
        ("m", "acme", "1700000000", b'{"tenant":"acme"}'),
        ("another-master", "globex_2", "1", b""),
        ("m", "nc-ai-gateway:evil", "1700000000", b"x"),
    ):
        if satellite["derive"](master, tenant) != addon["derive"](master, tenant):
            out.append(
                f"AI-gateway per-tenant KEY DERIVATION diverges for "
                f"tenant={tenant!r} (#373). The tenant side signs and the "
                f"satellite verifies; if these disagree, authentication breaks "
                f"in production while both test suites still pass, because each "
                f"recomputes its expectation with the same function it tests."
            )
            return
        if (satellite["sign"](master, tenant, stamp, body)
                != addon["sign"](master, tenant, stamp, body)):
            out.append(
                f"AI-gateway SIGNATURES diverge for tenant={tenant!r} (#373). "
                f"Same consequence as above: a 401 nobody can explain."
            )
            return


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

    # R5 — the command matcher must see both YAML forms and no other service's
    # command; the value matcher must accept only a real 0.
    # R8 — the anchored-pattern detector. It parses PYTHON SOURCE with a regex,
    # which makes it the most fragile rule here: if it silently stops matching,
    # main() prints "clean" over call sites it is no longer checking. That is
    # precisely what this self-test exists to prevent, and R8 shipped without
    # any until review pointed out every other regex rule had them.
    ("anchored", "DB_NAME_RE = re.compile(r'^[a-z][a-z0-9]{2,62}$')", True),
    ("anchored", 'EMAIL_RE = re.compile(r"^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$")', True),
    ("anchored", "_X = re.compile(r'^a$', re.IGNORECASE)", True),
    # Unanchored, or anchored at one end only: `.match()` on these is a
    # deliberate prefix test and must NOT be flagged.
    ("anchored", "PREFIX_RE = re.compile(r'^odoo\\.addons')", False),
    ("anchored", "SUFFIX_RE = re.compile(r'\\.py$')", False),
    ("anchored", "LOOSE_RE = re.compile(r'[a-z]+')", False),

    ("cron_cmd", "    command: odoo --log-level=debug --max-cron-threads=0", True),
    ("cron_cmd", '    command: ["odoo", "--proxy-mode", "--max-cron-threads=0"]', True),
    ("cron_cmd", "    command: postgres -c config_file=/etc/postgresql.conf", False),
    # A commented-out command must NOT match: the pattern anchors on
    # `^\s*command:`, and skipping comment LINES is separately the caller's job.
    ("cron_cmd", "    # command: odoo --max-cron-threads=0", False),
    ("cron_val", "--max-cron-threads=0", True),
    ("cron_val", '"--max-cron-threads=0"', True),
    ("cron_val", "--max-cron-threads=${NC_DEV_CRON_THREADS:-0}", True),
    # The two false negatives review reproduced against the first version.
    ("cron_val", "--max-cron-threads=64", False),
    ("cron_val", "--max-cron-threads=${NC_DEV_CRON_THREADS:-2}", False),
    ("cron_comment", "command: odoo  # was: --max-cron-threads=0, see #337", False),
    ("cron_comment", "command: odoo --max-cron-threads=0", True),

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
            actual = bool(_hardcoded_name_re(REPO_ROOT).search(line))
        elif name == "anchored":
            actual = bool(ANCHORED_COMPILE_RE.search(line))
        elif name == "cron_cmd":
            actual = bool(ODOO_COMMAND_RE.match(line))
        elif name == "cron_val":
            values = CRON_THREADS_VALUE_RE.findall(line)
            actual = bool(values) and all(
                CRON_THREADS_OK_RE.match(v) for v in values)
        elif name == "cron_comment":
            # The flag must survive comment-stripping to count.
            actual = bool(CRON_THREADS_VALUE_RE.findall(_strip_yaml_comment(line)))
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
    rule_cron_threads_scoped(findings)
    rule_cron_enumeration_declared(findings)
    rule_anchored_regex_uses_fullmatch(findings)
    rule_ai_gateway_kdf_agrees(findings)
    rule_workflow_actions_sha_pinned(findings)

    if findings:
        print("invariants: violations found\n")
        for f in findings:
            print(f"  ✗ {f}")
        print(f"\n{len(findings)} violation(s). Fix them, or — if genuinely blocked — add a")
        print("KNOWN_PENDING entry in scripts/ci/invariants.py naming the follow-up PR.")
        return 1

    print(f"invariants: clean ({len(paths)} file(s) scanned, {RULE_COUNT} rule(s) applied).")
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
