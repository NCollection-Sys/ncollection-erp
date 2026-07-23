#!/usr/bin/env bash
# ============================================================================
#  verify_pitr.sh — local end-to-end PITR proof  (P2-T04)
# ============================================================================
#  Proves restore to an ARBITRARY timestamp against a local repo:
#    1. full backup,
#    2. write marker 'before', capture time T,
#    3. write marker 'after', force-archive the WAL,
#    4. restore to T into a scratch instance,
#    5. boot the scratch instance and assert it shows the DB AS-OF T
#       (only 'before' — 'after' must be gone).
#  The destructive live-cluster + off-site (S3/B2) rehearsal runs on staging.
# ============================================================================
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 1

DC=(docker compose -f docker-compose.yml -f docker-compose.backup.yml)
SCRATCH="/tmp/pitr_verify"
PORT="5455"

pass=0
fail=0
ok(){ echo "  ✅ $1"; pass=$((pass + 1)); }
no(){ echo "  ❌ $1"; fail=$((fail + 1)); }
psql_main(){ "${DC[@]}" exec -T -u postgres db psql -U odoo -d postgres -tAc "$1"; }

echo "==> [1] full backup"
"${DC[@]}" exec -T -u postgres db pgbackrest --stanza=ncollection --type=full backup >/dev/null

echo "==> [2] write marker 'before', capture target time"
psql_main "DROP TABLE IF EXISTS pitr_marker; CREATE TABLE pitr_marker(tag text); INSERT INTO pitr_marker VALUES('before');" >/dev/null
sleep 2
TARGET="$(psql_main "SELECT now()")"
echo "     target = $TARGET"
sleep 2

echo "==> [3] write marker 'after' + wait until WAL past T is archived"
psql_main "INSERT INTO pitr_marker VALUES('after'); SELECT pg_switch_wal();" >/dev/null
# Async archiving: the restore to T is only valid once a WAL segment archived
# AFTER T has landed in the repo (guarantees the T-covering WAL is present).
archived=""
for _ in $(seq 1 30); do
  archived="$(psql_main "SELECT (last_archived_time > timestamptz '$TARGET') FROM pg_stat_archiver")"
  [ "$archived" = "t" ] && break
  psql_main "SELECT pg_switch_wal()" >/dev/null 2>&1
  sleep 2
done
if [ "$archived" = "t" ]; then echo "     WAL past T archived"; else no "WAL past T never archived (async lag)"; fi

echo "==> [4] restore to scratch at target time"
"${DC[@]}" exec -T -u postgres db bash -c "
  rm -rf $SCRATCH && mkdir -p $SCRATCH && chown postgres:postgres $SCRATCH &&
  pgbackrest --stanza=ncollection --type=time --target='$TARGET' \
    --target-action=promote --pg1-path=$SCRATCH restore
" || { no "pgbackrest restore failed"; }

echo "==> [5] boot scratch + assert as-of-T state"
rows="$("${DC[@]}" exec -T -u postgres db bash -c "
  pg_ctl -D $SCRATCH -o '-p $PORT -c archive_mode=off' -w start >/dev/null 2>&1
  psql -p $PORT -U odoo -d postgres -tAc \"SELECT string_agg(tag, ',' ORDER BY tag) FROM pitr_marker\" 2>/dev/null
  pg_ctl -D $SCRATCH stop >/dev/null 2>&1
" | tr -d '[:space:]')"
echo "     scratch pitr_marker = [$rows]"
if [ "$rows" = "before" ]; then
  ok "PITR restored the DB exactly as-of the target time (only 'before')"
else
  no "expected only 'before' as-of T, got [$rows]"
fi

"${DC[@]}" exec -T -u postgres db rm -rf "$SCRATCH" >/dev/null 2>&1

echo "------------------------------------------------------------"
echo "RESULT: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
