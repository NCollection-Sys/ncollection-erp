#!/usr/bin/env bash
# ============================================================================
#  stack_settled.sh — was the shared dev stack (db, odoo) just (re)started?
# ============================================================================
#  Cheap sanity check for REGRESSIONS.md R-018: background agents share ONE
#  Docker stack and ONE bind-mounted working tree, with no isolation between
#  them. Twice in one session an out-of-band container recreate / a RED-proof
#  edit landed mid-flight under a DIFFERENT concurrently-running agent and
#  produced a false CRITICAL (a fabricated tenant-isolation breach, a phantom
#  provisioning error) — neither reproduced on a clean re-run.
#
#  This does NOT prevent the race (see CLAUDE.md Rule 14 for that — destructive
#  work happens before fanning out background agents, never during). It gives
#  any agent about to report a scary finding a 2-second check on whether the
#  ground moved under it, instead of guessing.
#
#  Read-only. Never restarts/recreates anything itself.
#
#  Usage: scripts/dev/stack_settled.sh
#  Exit 0  "SETTLED"    — db + odoo have each been up for a while.
#  Exit 1  "UNSETTLED"  — one of them is down, or came up in roughly the last
#                         minute (`docker compose ps` still reports it in
#                         seconds) — treat any CRITICAL / isolation / auth /
#                         unreproducible finding right now as suspect, not
#                         proven; re-run it before trusting it.
# ============================================================================
set -uo pipefail
# No `set -e` — a missing/stopped service must not abort the report, it IS
# the report. Every path below is relative to the repo root.
cd "$(dirname "$0")/../.." || exit 1

unsettled=0

check_one() {
  local service="$1"
  # Derive from the compose service name, never a hardcoded container name
  # (CLAUDE.md Rule 11 / REGRESSIONS.md R-006).
  local status
  status="$(docker compose ps --format '{{.Status}}' "$service" 2>/dev/null || true)"
  if [ -z "$status" ]; then
    echo "UNSETTLED: $service is not running"
    unsettled=1
    return
  fi
  echo "$service: $status"
  if echo "$status" | grep -Eq '^Up [0-9]+ second'; then
    echo "  -> $service came up in roughly the last minute — just (re)started or recreated."
    unsettled=1
  fi
}

check_one db
check_one odoo

echo
if [ "$unsettled" -eq 0 ]; then
  echo "SETTLED"
  exit 0
fi

echo "STACK LOOKS UNSETTLED."
echo "Before reporting a CRITICAL / isolation / auth finding, or an error you can't"
echo "explain, right now — re-run whatever produced it once more. See"
echo "docs/markdown/REGRESSIONS.md R-018: both false CRITICALs this repo has hit did"
echo "NOT reproduce on a clean re-run; a real regression will."
exit 1
