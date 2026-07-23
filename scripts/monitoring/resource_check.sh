#!/usr/bin/env bash
# ============================================================================
#  resource_check.sh — disk / memory / CPU-load threshold alerts  (P2-T10)
# ============================================================================
#  Linux-server oriented (df / free / loadavg). Run from cron every few minutes.
#  Thresholds via env: MONITOR_DISK_PCT (85), MONITOR_MEM_PCT (90),
#  MONITOR_LOAD_PER_CPU (2.0 — 1-min loadavg per core).
# ============================================================================
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/monitoring/lib_alert.sh
. "$here/lib_alert.sh"

DISK_MAX="${MONITOR_DISK_PCT:-85}"
MEM_MAX="${MONITOR_MEM_PCT:-90}"
LOAD_PER_CPU="${MONITOR_LOAD_PER_CPU:-2.0}"
alerts=0

# Disk usage of the root filesystem.
disk="$(df -P / | awk 'NR==2 {gsub("%","",$5); print $5}')"
if [ -n "$disk" ] && [ "$disk" -ge "$DISK_MAX" ]; then
  nc_alert "🟠 Disk usage ${disk}% ≥ ${DISK_MAX}% on /"
  alerts=$((alerts + 1))
fi

# Memory usage (used / total). `free` is Linux-only; skip cleanly elsewhere.
if command -v free >/dev/null 2>&1; then
  mem="$(free | awk '/^Mem:/ {printf "%d", $3 * 100 / $2}')"
  if [ -n "$mem" ] && [ "$mem" -ge "$MEM_MAX" ]; then
    nc_alert "🟠 Memory usage ${mem}% ≥ ${MEM_MAX}%"
    alerts=$((alerts + 1))
  fi
fi

# 1-minute load average per CPU core.
if [ -r /proc/loadavg ] && command -v nproc >/dev/null 2>&1; then
  load1="$(awk '{print $1}' /proc/loadavg)"
  cores="$(nproc)"
  over="$(awk -v l="$load1" -v c="$cores" -v t="$LOAD_PER_CPU" \
          'BEGIN { print (l / c >= t) ? 1 : 0 }')"
  if [ "$over" = "1" ]; then
    nc_alert "🟠 Load ${load1} over ${cores} cores (≥ ${LOAD_PER_CPU}/core)"
    alerts=$((alerts + 1))
  fi
fi

if [ "$alerts" -eq 0 ]; then echo "✅ resources within thresholds"; fi
[ "$alerts" -eq 0 ]
