#!/usr/bin/env bash
# ============================================================================
#  pgbackrest_backup.sh — take a base backup  (P2-T04)
# ============================================================================
#  Schedule (cron on the server): weekly FULL, daily DIFF.
#      0 2 * * 0  .../pgbackrest_backup.sh full   # Sunday full
#      0 2 * * 1-6 .../pgbackrest_backup.sh diff   # Mon–Sat differential
#  Retention (2 full sets + WAL) is enforced automatically from pgbackrest.conf.
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")/../.." || exit 1

TYPE="${1:-full}"
case "$TYPE" in
  full|diff|incr) ;;
  *) echo "usage: $0 [full|diff|incr]" >&2; exit 1 ;;
esac

DC=(docker compose -f docker-compose.yml -f docker-compose.backup.yml)

echo "==> $TYPE backup starting"
"${DC[@]}" exec -T -u postgres db pgbackrest --stanza=ncollection --type="$TYPE" backup

echo "==> repository state"
"${DC[@]}" exec -T -u postgres db pgbackrest --stanza=ncollection info

echo "✅ $TYPE backup complete"
