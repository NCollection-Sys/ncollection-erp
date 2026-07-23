#!/usr/bin/env bash
# ============================================================================
#  wal_lag_check.sh — alert on WAL archive lag / failures  (P2-T04)
# ============================================================================
#  §5: "WAL archive lag > 5 min pages the team." Run from cron every few
#  minutes. Alerts to Discord (DISCORD_WEBHOOK) when the last successful
#  archive is older than the threshold OR pg_stat_archiver reports failures.
#  Feeds P2-T10 monitoring; superseded by the P8-T04 Prometheus exporter.
# ============================================================================
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 1

THRESHOLD="${WAL_LAG_THRESHOLD:-300}"   # seconds
DC=(docker compose -f docker-compose.yml -f docker-compose.backup.yml)

q(){ "${DC[@]}" exec -T db psql -U "${DB_USER:-odoo}" -d "${DB_NAME:-postgres}" -tAc "$1" 2>/dev/null; }

lag="$(q "SELECT COALESCE(EXTRACT(EPOCH FROM (now() - last_archived_time))::int, 999999) FROM pg_stat_archiver")"
failed="$(q "SELECT COALESCE(failed_count, 0) FROM pg_stat_archiver")"
lag="${lag:-999999}"
failed="${failed:-0}"

alert(){
  echo "!! WAL ARCHIVE ALERT: $1" >&2
  [ -n "${DISCORD_WEBHOOK:-}" ] && curl -fsS -X POST "$DISCORD_WEBHOOK" \
    -H 'Content-Type: application/json' \
    -d "{\"content\": \"🛑 WAL archive alert: $1\"}" >/dev/null
}

rc=0
if [ "$failed" -gt 0 ]; then alert "pg_stat_archiver reports $failed failed archive attempts"; rc=1; fi
if [ "$lag" -gt "$THRESHOLD" ]; then alert "last WAL archived ${lag}s ago (> ${THRESHOLD}s)"; rc=1; fi

if [ "$rc" -eq 0 ]; then echo "✅ WAL archiving healthy (lag ${lag}s, ${failed} failures)"; fi
exit "$rc"
