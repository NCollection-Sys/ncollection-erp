#!/usr/bin/env bash
# ============================================================================
#  #310 — cron-starvation proof (local; heavy — not CI)
# ============================================================================
#  Answers the issue's second acceptance criterion directly:
#
#    "verify the config-sync reconcile still runs on schedule while a
#     deliberately stalled fetch is in flight"
#
#  ---------------------------------------------------------------------------
#  WHY THIS EXISTS IN THIS SHAPE
#  ---------------------------------------------------------------------------
#  The unit test (tests/test_exchange_rate.py::test_the_cron_enqueues_and_does_
#  not_fetch_inline) proves the CODE no longer fetches on the cron thread. It
#  cannot prove the SYSTEM property the issue is about — that a stalled
#  outbound host does not delay other crons — because that property only exists
#  at the intersection of a real Odoo cron thread, max_cron_threads=1, and a
#  host that will not answer.
#
#  So this runs the real `ir.cron` scheduler against a real socket that stalls,
#  and MEASURES how long the config-sync reconcile cron waits.
#
#  ---------------------------------------------------------------------------
#  THE TWO ARMS — and why arm A is the point
#  ---------------------------------------------------------------------------
#  One measurement ("reconcile ran on time") proves nothing alone: it would
#  also pass on a machine with spare cron threads, if the stall never happened,
#  or if the harness measured the wrong thing. So both shapes go through
#  identical machinery and are REQUIRED TO DISAGREE:
#
#    arm A  "inline"  ir.cron code = model.refresh_ecb_rate()
#                     Exactly what _cron_refresh_ecb_rate did BEFORE #310 —
#                     fetch, parse and store on the cron thread. Not an
#                     approximation: #310 kept that body intact and public and
#                     only added the enqueue in front of it.
#                     EXPECTED: reconcile STARVED (delayed ~30s).
#
#    arm B  "queued"  ir.cron code = model._cron_refresh_ecb_rate()
#                     The shipped code. The cron enqueues and returns, while a
#                     genuinely stalled fetch runs CONCURRENTLY in a separate
#                     process standing in for the queue_job runner — so the
#                     issue's "while a stalled fetch is in flight" is literally
#                     true during the measurement.
#                     EXPECTED: reconcile ON TIME.
#
#  Arm A is the RED proof. If arm A stops showing starvation the harness has
#  lost the ability to detect the thing it exists to detect, and arm B's pass
#  becomes meaningless — so arm A failing to starve is a FAILURE here.
#
#  ---------------------------------------------------------------------------
#  HOW THE NUMBERS ARE OBTAINED — and why not from the database
#  ---------------------------------------------------------------------------
#  Both timestamps are parsed out of the harness Odoo's OWN log, and the metric
#  is the gap between the two CRONS — not the gap from process start:
#
#      T0  "Job 'NCollection: refresh ECB exchange rate' (n) starting"
#      T1  "Job 'NCollection: reconcile tenant workspace configs' (n) starting"
#
#  Two earlier versions of this measurement were wrong, both in the dangerous
#  direction (reporting starvation that had not happened):
#
#   1. Polling a marker row in the DATABASE. The shared dev `odoo` runs crons
#      for EVERY database on its server (see docker-compose.cronstall.yml), so
#      it was setting the marker itself on its own ~60s cycle and the harness
#      called the result starvation. Fixed by the private Postgres + reading
#      this process's own log, which nothing else can write.
#
#   2. Timing from "Modules loaded.". Odoo's cron thread blocks in select() for
#      up to SLEEP_INTERVAL (60s) before its FIRST poll, so that constant landed
#      in both arms: arm B measured 61s for a reconcile that had actually run in
#      the same tick as the fetch. Cron-to-cron removes the scheduler's own
#      startup latency, which is not what #310 is about.
#
#  ---------------------------------------------------------------------------
#  HOW THE STALL IS PRODUCED
#  ---------------------------------------------------------------------------
#  A container accepts TCP on :443 and says nothing, so the TLS handshake never
#  completes and `requests` blocks for its full read timeout (_RPC_TIMEOUT =
#  30s; measured 30.1s). The harness Odoo's own /etc/hosts sends
#  www.ecb.europa.eu there.
#
#  _ECB_URL is NOT touched — the "single allowlisted host" control stays as
#  shipped. This measures the 30s IDLE-timeout stall; the real worst case is
#  larger (_RPC_DEADLINE = 60s total, ~90s for deadline plus one idle gap per
#  config_sync.py), which would only widen the gap between the arms.
#
#  ---------------------------------------------------------------------------
#  SHARED-STACK SAFETY (CLAUDE.md Rule 14 / R-018)
#  ---------------------------------------------------------------------------
#  Self-contained: own Postgres, own volumes, own network, source tree mounted
#  READ-ONLY. It does not require the dev stack and cannot disturb it. The RED
#  arm is an ir.cron row in the harness database, never a patched checkout.
#
#  Idempotent (Rule 12): re-running is a no-op — the database is reused and
#  re-upgraded, both arms re-arm from scratch. Proven by running it twice.
#
#  Usage:  make cron-starvation-verify        (add KEEP=1 to keep the stack up)
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")/../../../.."   # repo root

HARNESS_DB="${HARNESS_DB:-cronstall}"
DB_USER="${DB_USER:-odoo}"
DB_PASSWORD="${DB_PASSWORD:-odoo}"

DCH=(docker compose -f docker-compose.yml -f docker-compose.cronstall.yml)
DBARGS=(--db_host=cron-stall-db --db_user="$DB_USER" --db_password="$DB_PASSWORD")

# A stalled fetch blocks ~30s (_RPC_TIMEOUT). Thresholds sit well inside that
# so ordinary scheduling jitter cannot flip either verdict.
MIN_STARVATION=20     # arm A must be delayed AT LEAST this long
MAX_ONTIME=10         # arm B must be delayed AT MOST this long
SERVER_TTL=120        # how long each arm's Odoo is allowed to run

pass=0; fail=0
ok(){ echo "  ✅ PASS: $1"; pass=$((pass + 1)); }
no(){ echo "  ❌ FAIL: $1"; fail=$((fail + 1)); }
hr(){ echo "----------------------------------------------------------------------"; }

WORK="$(mktemp -d)"
# Deterministic names: run_arm is called inside a command substitution, so a
# name it recorded in a variable would be lost with that subshell.
C_INLINE="nccronstall-inline-$$"
C_QUEUED="nccronstall-queued-$$"
C_INFLIGHT="nccronstall-inflight-$$"

cleanup(){
  local c
  for c in "$C_INLINE" "$C_QUEUED" "$C_INFLIGHT"; do
    docker rm -f "$c" >/dev/null 2>&1 || true
  done
  if [ -z "${KEEP:-}" ]; then
    "${DCH[@]}" rm -sf cron-stall-host cron-stall-db >/dev/null 2>&1 || true
  fi
  rm -rf "$WORK"
}
trap cleanup EXIT

# `hq SQL` — one scalar out of the harness database. `-d` is explicit (R1).
hq(){ "${DCH[@]}" exec -T cron-stall-db psql -U "$DB_USER" -d "$HARNESS_DB" -tAc "$1" 2>/dev/null | tr -d '[:space:]'; }

# `log_delay <logfile>` — seconds between the ECB cron STARTING and the
# config-sync reconcile cron STARTING, from Odoo's own log timestamps. Prints
# -1 if either line is absent.
#
# Measured cron-to-cron, NOT from process start, and that matters. Odoo's cron
# thread waits on select() for up to SLEEP_INTERVAL (60s) before its first
# poll, so timing from "Modules loaded." adds a ~60s constant to BOTH arms that
# has nothing to do with #310 — it made arm B read 61s when the reconcile had
# in fact run in the same tick as the fetch. The gap between the two crons is
# exactly the quantity the issue is about: how long the reconcile waits behind
# the outbound fetch.
#
# Timestamps are `HH:MM:SS,mmm` in field 2; the 86400 wrap keeps a run that
# straddles midnight from going negative.
log_delay(){
  awk '
    function secs(f,  t) { split(f, t, /[:,]/); return t[1]*3600 + t[2]*60 + t[3] }
    /refresh ECB exchange rate.* starting/ { if (t0 == "") t0 = secs($2) }
    /reconcile tenant workspace configs.* starting/ {
      if (t0 != "" && t1 == "") t1 = secs($2)
    }
    END {
      if (t0 == "" || t1 == "") { print -1; exit }
      d = t1 - t0; if (d < 0) d += 86400; print d
    }' "$1"
}

echo "======================================================================"
echo " #310 cron-starvation proof — private stack, database '$HARNESS_DB'"
echo "======================================================================"

# --- bring up the harness's own Postgres ------------------------------------
echo "== starting the harness's private Postgres =="
"${DCH[@]}" up -d --wait cron-stall-db >/dev/null
echo "  cron-stall-db healthy"

# --- the harness database ---------------------------------------------------
# Created on first run, REUSED and re-upgraded afterwards — same contract as
# verify_config_sync.sh's platform_schema_sync, and for the same reason: a
# persistent fixture on a stale schema reports a code failure that is really a
# setup problem. Fail loud (Rule 10); never `|| true` this.
if [ "$(hq "SELECT 1")" = "1" ]; then
  echo "== reusing '$HARNESS_DB' (upgrading ncollection_saas to match the code) =="
  setup_args=(-u ncollection_saas)
else
  echo "== creating '$HARNESS_DB' (installing ncollection_saas — first run is slow) =="
  setup_args=(-i ncollection_saas)
fi
if ! "${DCH[@]}" run --rm -T cron-stall-odoo \
     odoo -d "$HARNESS_DB" "${setup_args[@]}" \
     --stop-after-init --no-http --log-level=warn "${DBARGS[@]}" >"$WORK/setup.log" 2>&1; then
  echo "REFUSING: could not install/upgrade ncollection_saas on '$HARNESS_DB'." >&2
  tail -25 "$WORK/setup.log" >&2
  exit 1
fi
hr

# --- the stall host ---------------------------------------------------------
echo "== starting the stall host (accepts :443, never answers) =="
"${DCH[@]}" up -d cron-stall-host >/dev/null
for _ in $(seq 1 30); do
  if "${DCH[@]}" logs cron-stall-host 2>&1 | grep -q "listening on :443"; then break; fi
  sleep 1
done
if ! "${DCH[@]}" logs cron-stall-host 2>&1 | grep -q "listening on :443"; then
  echo "REFUSING: the stall host never reported listening — every measurement" >&2
  echo "  below would be against a host that answers normally, i.e. no stall." >&2
  exit 1
fi
echo "  stall host up"
hr

# ---------------------------------------------------------------------------
#  arm_setup <ecb-cron-code>
# ---------------------------------------------------------------------------
#  Every OTHER cron is deactivated on purpose: _process_jobs_loop runs ready
#  jobs SEQUENTIALLY in the one cron thread, so an unrelated cron landing in
#  the same tick would add its runtime to the measured delay.
#
#  priority orders the two — _get_all_ready_jobs is `ORDER BY failure_count,
#  priority, id`, so ECB (1) is acquired before reconcile (5). That ordering IS
#  the scenario: the reconcile is the job stuck behind the fetch.
# ---------------------------------------------------------------------------
arm_setup(){
  local ecb_code="$1"
  "${DCH[@]}" run --rm -T cron-stall-odoo \
    odoo shell -d "$HARNESS_DB" --no-http --log-level=error "${DBARGS[@]}" \
    >"$WORK/setup_arm.log" 2>&1 <<PY
from datetime import datetime, timedelta
past = (datetime.now() - timedelta(minutes=5)).strftime('%Y-%m-%d %H:%M:%S')

# A queued fetch left by an earlier arm would be deduplicated by identity_key,
# masking a regression in the enqueue path itself.
env.cr.execute("DELETE FROM queue_job")
env['ir.cron'].sudo().search([]).write({'active': False})

env.ref('ncollection_saas.cron_ecb_rate_refresh').sudo().write({
    'active': True, 'priority': 1, 'nextcall': past, 'code': """$ecb_code""",
})
env.ref('ncollection_saas.cron_config_reconcile').sudo().write({
    'active': True, 'priority': 5, 'nextcall': past,
    'code': "model._cron_reconcile_config()",
})
env.cr.commit()
print('ARMED')
PY
  if ! grep -q ARMED "$WORK/setup_arm.log"; then
    echo "REFUSING: could not arm the crons — the measurement would be vacuous." >&2
    tail -25 "$WORK/setup_arm.log" >&2
    exit 1
  fi
}

# ---------------------------------------------------------------------------
#  start_inflight_fetch — stand in for the queue_job runner (arm B only)
# ---------------------------------------------------------------------------
#  Arm B's cron only ENQUEUES, so without this nothing would actually be
#  stalled and the arm would prove much less than the issue asks. This performs
#  the same refresh_ecb_rate() the runner would, in its own process, against
#  the stall host. It logs a line before blocking so the caller can wait for
#  the stall to have STARTED rather than guess with a sleep.
# ---------------------------------------------------------------------------
start_inflight_fetch(){
  "${DCH[@]}" run --rm -T --name "$C_INFLIGHT" cron-stall-odoo \
    odoo shell -d "$HARNESS_DB" --no-http --log-level=error "${DBARGS[@]}" \
    >"$WORK/inflight.log" 2>&1 <<'PY' &
print('INFLIGHT-FETCH-STARTING', flush=True)
env['ncollection.exchange.rate'].refresh_ecb_rate()
PY
  local waited=0
  while [ "$waited" -lt 90 ]; do
    if grep -q "INFLIGHT-FETCH-STARTING" "$WORK/inflight.log" 2>/dev/null; then return 0; fi
    sleep 1; waited=$((waited + 1))
  done
  echo "REFUSING: the stand-in runner never reached the fetch, so nothing was" >&2
  echo "  in flight — arm B would not be testing the issue's condition." >&2
  exit 1
}

# ---------------------------------------------------------------------------
#  run_arm <name> <container> <ecb-cron-code> <inflight yes|no> -> the delay
# ---------------------------------------------------------------------------
run_arm(){
  local arm="$1" cname="$2" ecb_code="$3" want_inflight="$4"
  local logf="$WORK/$arm.log"

  arm_setup "$ecb_code"
  if [ "$want_inflight" = "yes" ]; then start_inflight_fetch; fi

  # --max-cron-threads=1 reproduces the production constraint the issue names.
  # A CLI flag deliberately: it OVERRIDES the conf, which is finding #1 of this
  # ticket (odoo-bus pins it the same way, so editing odoo.prod.conf is inert).
  "${DCH[@]}" run --rm -T --name "$cname" cron-stall-odoo \
    timeout "$SERVER_TTL" odoo -d "$HARNESS_DB" --no-http --max-cron-threads=1 \
    --log-level=info "${DBARGS[@]}" >"$logf" 2>&1 &
  local runpid=$!

  local waited=0
  while [ "$waited" -lt "$SERVER_TTL" ]; do
    if grep -q "Modules loaded" "$logf" 2>/dev/null; then break; fi
    sleep 1; waited=$((waited + 1))
  done

  # ACCELERATOR — deliberately not load-bearing. Odoo's cron thread blocks in
  # select() for up to SLEEP_INTERVAL (60s) before its first poll, which would
  # add a minute of dead wall-clock to each arm. It also LISTENs on
  # 'cron_trigger', so a NOTIFY wakes it immediately. If every NOTIFY here is
  # missed (e.g. sent before the LISTEN is issued), the 60s timeout still fires
  # and the arm still measures correctly — only slower. Never assert on it.
  local n=0
  while [ "$n" -lt 15 ]; do
    if grep -q "refresh ECB exchange rate.* starting" "$logf" 2>/dev/null; then break; fi
    "${DCH[@]}" exec -T cron-stall-db psql -U "$DB_USER" -d postgres \
      -c "NOTIFY cron_trigger, '$HARNESS_DB'" >/dev/null 2>&1 || true
    sleep 1; n=$((n + 1))
  done

  # Wait for the reconcile cron to be logged, or for the server to time out.
  waited=0
  while [ "$waited" -lt "$SERVER_TTL" ]; do
    if grep -q "reconcile tenant workspace configs.* starting" "$logf" 2>/dev/null; then break; fi
    sleep 1; waited=$((waited + 1))
  done

  docker rm -f "$cname" >/dev/null 2>&1 || true
  wait "$runpid" 2>/dev/null || true
  if [ "$want_inflight" = "yes" ]; then
    docker rm -f "$C_INFLIGHT" >/dev/null 2>&1 || true
  fi
  log_delay "$logf"
}

# --- arm A: the pre-#310 shape ----------------------------------------------
echo "== arm A — INLINE fetch on the cron thread (the pre-#310 shape) =="
echo "   expecting the reconcile to be STARVED (>= ${MIN_STARVATION}s)"
delay_a="$(run_arm inline "$C_INLINE" 'model.refresh_ecb_rate()' no)"
echo "   reconcile started ${delay_a}s after the ECB cron started"
if [ "$delay_a" -lt 0 ]; then
  no "arm A: the reconcile cron never ran at all within ${SERVER_TTL}s"
elif [ "$delay_a" -ge "$MIN_STARVATION" ]; then
  ok "arm A: stalled fetch STARVED the reconcile by ${delay_a}s (harness can detect it)"
else
  no "arm A: expected starvation >= ${MIN_STARVATION}s, measured ${delay_a}s.
      The harness did NOT reproduce the problem, so arm B proves nothing.
      Check that the stall host is really intercepting www.ecb.europa.eu."
fi
hr

# --- arm B: the shipped shape -----------------------------------------------
echo "== arm B — SHIPPED code: cron enqueues, real stalled fetch in flight =="
echo "   expecting the reconcile to run ON TIME (<= ${MAX_ONTIME}s)"
delay_b="$(run_arm queued "$C_QUEUED" 'model._cron_refresh_ecb_rate()' yes)"
echo "   reconcile started ${delay_b}s after the ECB cron started"
if [ "$delay_b" -lt 0 ]; then
  no "arm B: the reconcile cron never ran at all within ${SERVER_TTL}s"
elif [ "$delay_b" -le "$MAX_ONTIME" ]; then
  ok "arm B: reconcile ran on schedule (${delay_b}s) with a stalled fetch in flight"
else
  no "arm B: reconcile was delayed ${delay_b}s (limit ${MAX_ONTIME}s) — outbound
      work is still occupying the cron thread. This is #310, unfixed."
fi

# The enqueue must be visible, or arm B passed only because nothing happened.
queued_rows="$(hq "SELECT COUNT(*) FROM queue_job WHERE channel='root.outbound'")"
if [ "$queued_rows" = "1" ]; then
  ok "the fetch was queued on the dedicated 'root.outbound' channel"
else
  no "expected exactly 1 queue_job row on 'root.outbound', found '${queued_rows}'"
fi
hr

# --- the comparison ---------------------------------------------------------
if [ "$delay_a" -ge 0 ] && [ "$delay_b" -ge 0 ]; then
  gap=$((delay_a - delay_b))
  echo "== arm A ${delay_a}s vs arm B ${delay_b}s — #310 removes ${gap}s of starvation =="
  if [ "$gap" -ge "$MIN_STARVATION" ]; then
    ok "moving the fetch off the cron thread recovered ${gap}s of cron latency"
  else
    no "the two arms differ by only ${gap}s — the fix is not doing what it claims"
  fi
fi
hr

echo "SUMMARY: $pass passed, $fail failed."
if [ "$fail" -eq 0 ]; then
  echo "✅ #310 verified: a stalled outbound fetch no longer delays the"
  echo "   config-sync reconcile cron (arm A proves the harness sees starvation;"
  echo "   arm B proves the shipped code does not suffer it)."
else
  echo "❌ FAILURES above."
  exit 1
fi
