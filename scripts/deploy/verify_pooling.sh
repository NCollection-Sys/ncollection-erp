#!/usr/bin/env bash
# ============================================================================
#  verify_pooling.sh — prove the P2-T09 topology  (ARCHITECTURE_DATA_PLATFORM §4.2)
# ============================================================================
#  Run with the pooled stack up:
#     docker compose -f docker-compose.yml -f docker-compose.prod.yml \
#                    -f docker-compose.pooling.yml up -d db pgbouncer odoo odoo-bus
#     ./scripts/deploy/verify_pooling.sh
#
#  Proves: HTTP serves THROUGH PgBouncer, the pool is visible in transaction
#  mode (SHOW POOLS — the P2-T10 monitoring hook), and the bus/cron node runs
#  DIRECT. Full chatter delivery over nginx is proven on staging (needs the edge).
# ============================================================================
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 1

# docker-compose.pooling.yml mounts ./oca, and this script is invoked directly
# (its own header documents a raw `docker compose ... up -d`), never through a
# make target — so the Makefile-level guards cannot reach it (#384).
./scripts/dev/assert_oca_present.sh "the pooling stack"

DC=(docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.pooling.yml)
DB_USER="${DB_USER:-odoo}"
DB_PASSWORD="${DB_PASSWORD:-odoo}"

pass=0
fail=0
ok(){ echo "  ✅ PASS: $1"; pass=$((pass + 1)); }
no(){ echo "  ❌ FAIL: $1"; fail=$((fail + 1)); }
hr(){ echo "------------------------------------------------------------"; }

pgb(){  # run a pgbouncer admin query from the db container (which ships the client)
  "${DC[@]}" exec -T -e PGPASSWORD="$DB_PASSWORD" db \
    psql -h pgbouncer -p 6432 -U "$DB_USER" -d pgbouncer -tAc "$1" 2>/dev/null
}

hr; echo "A. HTTP serves through PgBouncer"; hr
if "${DC[@]}" exec -T odoo curl -sf http://localhost:8069/web/health >/dev/null; then
  ok "odoo HTTP /web/health 200 (DB path = pgbouncer:6432)"
else
  no "odoo HTTP not healthy"
fi

hr; echo "B. Pool visible + transaction mode (SHOW POOLS / SHOW CONFIG)"; hr
pools="$(pgb 'SHOW POOLS')"
if [ -n "$pools" ]; then
  ok "SHOW POOLS returns rows"
  while IFS= read -r line; do echo "      $line"; done <<< "$pools"
else
  no "SHOW POOLS empty/unreachable"
fi
mode="$(pgb 'SHOW CONFIG' | grep -i '^pool_mode')"
if echo "$mode" | grep -qi transaction; then ok "pool_mode = transaction"; else no "pool_mode not transaction ($mode)"; fi

hr; echo "C. Bus + cron run DIRECT (odoo-bus)"; hr
if "${DC[@]}" exec -T odoo-bus curl -sf http://localhost:8069/web/health >/dev/null; then
  ok "odoo-bus alive (serves :8072 bus + cron, DIRECT to Postgres)"
else
  no "odoo-bus not healthy"
fi

hr
echo "RESULT: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
