#!/usr/bin/env bash
# ============================================================================
#  health_check.sh — probe Odoo + tenant subdomains, alert on failure  (P2-T10)
# ============================================================================
#  The acceptance path: run from cron every minute. If the Odoo container dies,
#  its health URL stops answering and a Discord alert fires within ~1 minute.
#
#  MONITOR_HEALTH_URLS: space-separated URLs (admin + tenant subdomains).
#  Default probes the local Odoo /web/health (no-database 200 route).
# ============================================================================
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/monitoring/lib_alert.sh
. "$here/lib_alert.sh"

read -ra URLS <<< "${MONITOR_HEALTH_URLS:-http://localhost:8069/web/health}"

fails=0
for url in "${URLS[@]}"; do
  if curl -sf --max-time 5 "$url" >/dev/null 2>&1; then
    echo "OK  $url"
  else
    nc_alert "🔴 Health check FAILED — $url is unreachable"
    fails=$((fails + 1))
  fi
done

if [ "$fails" -eq 0 ]; then echo "✅ all health probes OK"; fi
[ "$fails" -eq 0 ]
