#!/usr/bin/env bash
# ============================================================================
#  log_watcher.sh — alert on Odoo ERROR/CRITICAL log lines  (P2-T10)
# ============================================================================
#  Scans the Odoo container's recent logs for error-level entries. Run from cron
#  slightly more often than the window so nothing is missed and little repeats.
#  MONITOR_LOG_SERVICE (odoo), MONITOR_LOG_SINCE (5m).
# ============================================================================
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/monitoring/lib_alert.sh
. "$here/lib_alert.sh"
cd "$here/../.." || exit 1

SVC="${MONITOR_LOG_SERVICE:-odoo}"
SINCE="${MONITOR_LOG_SINCE:-5m}"

logs="$(docker compose logs "$SVC" --since "$SINCE" --no-color 2>/dev/null)"
count="$(printf '%s\n' "$logs" | grep -cE "[0-9] (ERROR|CRITICAL) ")"

if [ "${count:-0}" -gt 0 ]; then
  sample="$(printf '%s\n' "$logs" | grep -E "[0-9] (ERROR|CRITICAL) " | tail -1)"
  nc_alert "🟠 Odoo logged ${count} ERROR/CRITICAL line(s) in the last ${SINCE}. Latest: ${sample}"
  exit 1
fi
echo "✅ no ERROR/CRITICAL in the last ${SINCE}"
