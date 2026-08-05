#!/usr/bin/env bash
# =============================================================================
# #343 — `provisioning-runner` must run the crons of the PLATFORM database
#        only, not of every database on the server.
#
# THE BUG. Odoo picks cron databases with
#     cron_database_list() = config['db_name'] or list_dbs(True)
# and `--db-filter` is not part of that expression — it filters HTTP routing
# only. `provisioning-runner` carried a db-filter and no `-d`, so it ticked
# every database on the server: the exact finding of #337, in a second
# container that PR deliberately left alone.
#
# WHY THE #337 FIX IS THE WRONG ONE HERE. The dev and routing containers were
# fixed with `--max-cron-threads=0`, because they are not supposed to run cron
# at all. This one is: the config-sync reconcile job lives here (see
# docker-compose.saas.yml). Setting 0 would stop that job silently — a delayed,
# invisible failure, which is the shape of outage this repo keeps re-learning.
# So the fix is to SCOPE cron with `-d`, not to disable it.
#
# WHAT IS MEASURED, AND WHY NOT THE LOG. Cron execution is observed through
# `ir_cron.nextcall`: a job that runs has its next occurrence moved forward.
# That is the database's own record of what happened, not a log line whose
# wording is an Odoo implementation detail. Both databases are seeded with a
# cron due in the PAST, so "did not run" cannot be confused with "was not due
# yet" — the failure mode a naive version of this test would sail past.
#
# TWO ARMS, one flag apart:
#   A (RED)   — the pre-#343 command shape. MUST tick both databases.
#   B (GREEN) — the shipped shape, with `-d`. MUST tick the platform DB only.
#
# Arm A is what makes this a proof. Without it, a harness that never ticks
# anything (wrong image, dead container, cron off) reports GREEN.
#
# Isolation: private Postgres, private network, nothing published. Arm A
# deliberately runs other databases' crons, so it must never be pointed at the
# shared dev `db` — CLAUDE.md Rule 14.
# =============================================================================
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../../../.."

PLATFORM_DB="cronscopeplatform"
TENANT_DB="cronscopetenant"
DC=(docker compose -f docker-compose.yml -f docker-compose.cronscope.yml)
DB_USER_EFF="${DB_USER:-odoo}"

# How long each arm is allowed to run. Odoo's cron loop sleeps SLEEP_INTERVAL
# (60s) between passes, so an arm shorter than that can miss a tick it should
# have seen and report a false GREEN for arm A.
ARM_SECONDS="${NC_CRON_SCOPE_ARM_SECONDS:-100}"

pass() { printf '  \033[32m✓\033[0m %s\n' "$1"; }
fail() { printf '  \033[31m✗\033[0m %s\n' "$1"; FAILURES=$((FAILURES + 1)); }
FAILURES=0

psql_q() { # psql_q <db> <sql> -> single value, whitespace-trimmed
  "${DC[@]}" exec -T cron-scope-db \
    psql -qtAX -U "$DB_USER_EFF" -d "$1" -c "$2" 2>/dev/null | tr -d '[:space:]'
}

# Invoked through the EXIT trap below, which shellcheck cannot see.
# shellcheck disable=SC2329
cleanup() {
  "${DC[@]}" rm -sf cron-scope-runner >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "=============================================================="
echo "#343 — provisioning-runner cron scope"
echo "=============================================================="

# --- 1. private stack -------------------------------------------------------
echo
echo "[1/5] Bringing up the private Postgres…"
"${DC[@]}" up -d --wait cron-scope-db
pass "private Postgres healthy (nothing published to the host)"

# --- 2. two databases -------------------------------------------------------
echo
echo "[2/5] Seeding two databases (platform + a decoy tenant)…"
for db in "$PLATFORM_DB" "$TENANT_DB"; do
  if psql_q postgres "SELECT 1 FROM pg_database WHERE datname='$db'" | grep -q 1; then
    echo "      $db already present, reusing"
    continue
  fi
  echo "      creating $db (odoo -i base, ~1 min)…"
  "${DC[@]}" run --rm -T cron-scope-runner \
    odoo -c /etc/odoo/odoo.conf -d "$db" -i base \
         --without-demo=all --stop-after-init --max-cron-threads=0 \
    >/dev/null 2>&1
done
pass "$PLATFORM_DB and $TENANT_DB exist on the private server"

# --- 3. make a cron due in the past, in BOTH -------------------------------
# Without this both databases would sit with future nextcalls and neither arm
# would tick anything — arm B would pass for the wrong reason.
echo
echo "[3/5] Forcing one cron due in the past in each database…"
seed_due() {
  "${DC[@]}" exec -T cron-scope-db psql -qtAX -U "$DB_USER_EFF" -d "$1" -c "
    UPDATE ir_cron SET nextcall = now() - interval '1 hour', active = true
     WHERE id = (SELECT id FROM ir_cron ORDER BY id LIMIT 1);" >/dev/null
}
read_nextcall() { psql_q "$1" "SELECT nextcall FROM ir_cron ORDER BY id LIMIT 1"; }

run_arm() { # run_arm <label> <extra-args...>
  local label="$1"; shift
  seed_due "$PLATFORM_DB"; seed_due "$TENANT_DB"
  local before_p before_t after_p after_t
  before_p="$(read_nextcall "$PLATFORM_DB")"
  before_t="$(read_nextcall "$TENANT_DB")"

  "${DC[@]}" rm -sf cron-scope-runner >/dev/null 2>&1 || true
  # Capture the ID compose prints rather than naming the container: a derived
  # ID cannot be wrong under a non-default COMPOSE_PROJECT_NAME (Rule 11 /
  # R-006), and there is no name for a later run to collide with.
  local cid
  cid="$("${DC[@]}" run -d cron-scope-runner \
    odoo -c /etc/odoo/odoo.conf --load=base,web \
         --workers=0 --max-cron-threads=1 "$@")"
  if [ -z "$cid" ]; then
    fail "arm $label: the runner container did not start; nothing was measured."
    return 1
  fi
  sleep "$ARM_SECONDS"
  docker rm -f "$cid" >/dev/null 2>&1 || true

  after_p="$(read_nextcall "$PLATFORM_DB")"
  after_t="$(read_nextcall "$TENANT_DB")"
  ARM_PLATFORM_TICKED=$([ "$before_p" != "$after_p" ] && echo yes || echo no)
  ARM_TENANT_TICKED=$([ "$before_t" != "$after_t" ] && echo yes || echo no)
}
pass "both databases hold a cron whose nextcall is in the past"

# --- 4. arm A — the pre-#343 shape MUST leak -------------------------------
echo
echo "[4/5] Arm A (RED): the pre-#343 command — db-filter, no -d…"
run_arm reda --db-filter="^${PLATFORM_DB}\$"
echo "      platform ticked=$ARM_PLATFORM_TICKED  tenant ticked=$ARM_TENANT_TICKED"
if [ "$ARM_TENANT_TICKED" = "yes" ]; then
  pass "arm A ticked the DECOY TENANT database — the bug reproduces, so this"
  pass "  harness can actually observe it (db_filter does not restrict cron)"
else
  fail "arm A did NOT tick the tenant database. The harness cannot see the bug"
  fail "  it exists to prove, so arm B's result below means nothing. Check the"
  fail "  arm duration (NC_CRON_SCOPE_ARM_SECONDS=$ARM_SECONDS, Odoo sleeps 60s"
  fail "  between cron passes) before believing any GREEN from this run."
fi

# --- 5. arm B — the shipped shape must NOT leak ----------------------------
echo
echo "[5/5] Arm B (GREEN): the shipped command — db-filter AND -d…"
run_arm greenb -d "$PLATFORM_DB" --db-filter="^${PLATFORM_DB}\$"
echo "      platform ticked=$ARM_PLATFORM_TICKED  tenant ticked=$ARM_TENANT_TICKED"
if [ "$ARM_PLATFORM_TICKED" = "yes" ]; then
  pass "the platform database's cron still runs — the config-sync reconcile"
  pass "  job that lives in this container is NOT silently stopped"
else
  fail "the platform database's cron did NOT run. Scoping must not disable the"
  fail "  reconcile job — that is precisely why --max-cron-threads=0 was the"
  fail "  wrong fix here."
fi
if [ "$ARM_TENANT_TICKED" = "no" ]; then
  pass "the decoy tenant database was left alone — cron is scoped by -d"
else
  fail "the tenant database was still ticked WITH -d present. Scoping failed."
fi

echo
if [ "$FAILURES" -eq 0 ]; then
  echo "✅ #343 verified: provisioning-runner runs the platform DB's crons only,"
  echo "   and still runs them."
else
  echo "❌ $FAILURES check(s) failed."
fi
exit $((FAILURES > 0))
