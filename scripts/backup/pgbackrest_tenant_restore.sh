#!/usr/bin/env bash
# ============================================================================
#  pgbackrest_tenant_restore.sh — restore ONE tenant DB to a point in time  (P2-T04)
# ============================================================================
#  §5 nuance: PITR restores the CLUSTER, not a single database. So:
#    1. restore the cluster to a SCRATCH instance at time T,
#    2. boot the scratch instance on a spare port,
#    3. pg_dump the ONE tenant DB from it,
#    4. (operator-confirmed, destructive) restore that dump into the live cluster.
#  This script does 1–3 and prints the exact step-4 command — it never writes to
#  the live cluster on its own.
#      pgbackrest_tenant_restore.sh <tenant_db> '<target-time>' [out.dump]
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")/../.." || exit 1

TENANT="${1:-}"
TARGET_TIME="${2:-}"
OUT="${3:-/tmp/${TENANT}_pitr.dump}"
SCRATCH="/tmp/tenant_pitr_scratch"
PORT="5433"
[ -n "$TENANT" ] && [ -n "$TARGET_TIME" ] || {
  echo "usage: $0 <tenant_db> '<YYYY-MM-DD HH:MM:SS+TZ>' [out.dump]" >&2; exit 1; }

DC=(docker compose -f docker-compose.yml -f docker-compose.backup.yml)

echo "==> [1/3] restore cluster to scratch at '$TARGET_TIME'"
"${DC[@]}" exec -T -u postgres db bash -c "
  rm -rf '$SCRATCH' && mkdir -p '$SCRATCH' &&
  pgbackrest --stanza=ncollection --type=time --target='$TARGET_TIME' \
    --target-action=promote --pg1-path='$SCRATCH' restore
"

echo "==> [2/3] boot scratch + [3/3] dump tenant '$TENANT' -> $OUT"
"${DC[@]}" exec -T -u postgres db bash -c "
  pg_ctl -D '$SCRATCH' -o '-p $PORT -c archive_mode=off' -w start &&
  pg_dump -p $PORT -U odoo --format=custom -d '$TENANT' -f '$OUT' ;
  rc=\$? ;
  pg_ctl -D '$SCRATCH' stop ;
  exit \$rc
"

echo "✅ Tenant '$TENANT' as-of '$TARGET_TIME' dumped to (container) $OUT"
echo "   To restore INTO the live cluster (DESTRUCTIVE — confirm first):"
echo "     docker compose ... exec -u postgres db bash -c \\"
echo "       'dropdb -U odoo --if-exists $TENANT && createdb -U odoo $TENANT && \\"
echo "        pg_restore -U odoo -d $TENANT $OUT'"
