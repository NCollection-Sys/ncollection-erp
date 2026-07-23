#!/usr/bin/env bash
# ============================================================================
#  verify_monitoring.sh — prove the P2-T10 acceptance  (local)
# ============================================================================
#  "killing the Odoo container triggers a Discord alert within 2 minutes."
#  Proven here without a webhook: nc_alert always logs "[ALERT] …", so we assert
#  that a healthy Odoo raises NO alert and a stopped Odoo DOES — within one probe
#  (cron runs it every minute, well inside the 2-minute bound). Restarts Odoo.
# ============================================================================
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 1

DC=(docker compose -f docker-compose.yml -f docker-compose.dev.yml)
HC=scripts/monitoring/health_check.sh
URL="http://localhost:8069/web/health"

pass=0
fail=0
ok(){ echo "  ✅ $1"; pass=$((pass + 1)); }
no(){ echo "  ❌ $1"; fail=$((fail + 1)); }

echo "==> [1] Odoo healthy → expect NO alert"
out="$(MONITOR_HEALTH_URLS="$URL" bash "$HC" 2>&1)"
if printf '%s' "$out" | grep -q '\[ALERT\]'; then no "unexpected alert while healthy"; else ok "no alert while healthy"; fi

echo "==> [2] stop Odoo → expect an alert"
"${DC[@]}" stop odoo >/dev/null 2>&1
out="$(MONITOR_HEALTH_URLS="$URL" bash "$HC" 2>&1)"
if printf '%s' "$out" | grep -q '\[ALERT\]'; then ok "alert fired when Odoo down"; else no "no alert when Odoo down"; fi

echo "==> [3] restart Odoo"
"${DC[@]}" start odoo >/dev/null 2>&1
for _ in $(seq 1 20); do
  if "${DC[@]}" ps odoo --format '{{.Status}}' 2>/dev/null | grep -q healthy; then break; fi
  sleep 3
done

echo "------------------------------------------------------------"
echo "RESULT: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
