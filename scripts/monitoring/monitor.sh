#!/usr/bin/env bash
# ============================================================================
#  monitor.sh — run every monitor once  (ticket P2-T10)
# ============================================================================
#  The single cron entrypoint: health + resources + Odoo logs + WAL-archive lag
#  (P2-T04). Each check is independent — one alerting never stops the others.
#  Exits non-zero if ANY check alerted (for cron mail / chaining).
#
#  Cron (every minute keeps the "kill Odoo → alert in 2 min" acceptance tight):
#      * * * * * cd /opt/ncollection && scripts/monitoring/monitor.sh
# ============================================================================
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)"

rc=0
"$here/health_check.sh"   || rc=1
"$here/resource_check.sh" || rc=1
"$here/log_watcher.sh"    || rc=1
# WAL-archive lag + failure alerting (built in P2-T04); only meaningful when the
# backup overlay is active — skips cleanly otherwise.
if [ -x "$here/../backup/wal_lag_check.sh" ]; then
  "$here/../backup/wal_lag_check.sh" || rc=1
fi

exit "$rc"
