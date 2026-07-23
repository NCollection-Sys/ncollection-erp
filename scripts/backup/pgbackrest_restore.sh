#!/usr/bin/env bash
# ============================================================================
#  pgbackrest_restore.sh — restore the cluster to a point in time  (P2-T04)
# ============================================================================
#  Restores to a SCRATCH data dir (never clobbers the live cluster) — the safe,
#  demonstrable PITR operation and the first half of a per-tenant restore.
#      pgbackrest_restore.sh '2026-07-22 14:30:00+00' [/tmp/pitr_scratch]
#  Boot the result to inspect it (see the printed command). A live in-place
#  restore is an incident procedure — see docs/markdown/RUNBOOK_BACKUP_PITR.md.
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")/../.." || exit 1

TARGET_TIME="${1:-}"
SCRATCH="${2:-/tmp/pitr_scratch}"
[ -n "$TARGET_TIME" ] || { echo "usage: $0 '<YYYY-MM-DD HH:MM:SS+TZ>' [scratch-path]" >&2; exit 1; }

DC=(docker compose -f docker-compose.yml -f docker-compose.backup.yml)

echo "==> Restoring stanza 'ncollection' to '$TARGET_TIME' into $SCRATCH"
"${DC[@]}" exec -T -u postgres db bash -c "
  rm -rf '$SCRATCH' && mkdir -p '$SCRATCH' &&
  pgbackrest --stanza=ncollection --type=time \
    --target='$TARGET_TIME' --target-action=promote \
    --pg1-path='$SCRATCH' restore
"

echo "✅ Restored to $SCRATCH. Boot it on a spare port to inspect:"
echo "   docker compose -f docker-compose.yml -f docker-compose.backup.yml \\"
echo "     exec -u postgres db pg_ctl -D $SCRATCH -o '-p 5433 -c archive_mode=off' start"
