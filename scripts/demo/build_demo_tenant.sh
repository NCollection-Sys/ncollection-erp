#!/usr/bin/env bash
# ============================================================================
#  make demo-tenant — build a populated demo workspace end to end (INFRA-07)
# ============================================================================
#  Produces "Al Barari Trading" at http://albarari.localhost with real GCC
#  business data, so the dashboard shows actual numbers instead of zeros.
#
#  Three stages:
#    1. platform DB with ncollection_saas (the SaaS control plane)
#    2. provision the tenant THROUGH the P2-T01 engine (plan -> tenant -> job),
#       which is the same path a real customer signup takes — the engine itself
#       is never modified, so `--without-demo=True` still holds and no paying
#       customer can ever receive Odoo demo data
#    3. seed curated GCC data (scripts/demo/seed_demo_data.py)
#
#  Idempotent: re-running skips what already exists. Use REBUILD=1 to drop the
#  tenant first and start clean.
#
#  Requires the routing stack: `make routing-up`.
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")/../.."   # repo root

PLATFORM_DB="${PLATFORM_DB:-ncplatform}"
DEMO_DB="${DEMO_DB:-albarari}"
DEMO_PW="${DEMO_ADMIN_PASSWORD:-demo1234}"
REBUILD="${REBUILD:-0}"

# The demo needs the nginx edge and db_filter, i.e. the routing overlay. Honour
# a caller-supplied COMPOSE_FILE (CI), else default to that trio.
if [ -n "${COMPOSE_FILE:-}" ]; then
  DC=(docker compose)
else
  DC=(docker compose -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.routing.yml)
fi
DBARGS=(--db_host=db --db_user=odoo --db_password=odoo)

db_cid="$("${DC[@]}" ps -q db)"
[ -n "$db_cid" ] || { echo "ERROR: db container not running. Start it: make routing-up" >&2; exit 1; }

psql_q() { docker exec "$db_cid" psql -U odoo -d "$1" -tAc "$2" 2>/dev/null; }
db_exists() {
  docker exec "$db_cid" psql -U odoo -d postgres -tAc \
    "SELECT 1 FROM pg_database WHERE datname='$1'" 2>/dev/null | grep -q 1
}
drop_db() {
  docker exec "$db_cid" psql -U odoo -d postgres -c \
    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$1'" >/dev/null 2>&1 || true
  docker exec "$db_cid" psql -U odoo -d postgres -c "DROP DATABASE IF EXISTS $1" >/dev/null 2>&1 || true
}

if [ "$REBUILD" = "1" ]; then
  echo "==> REBUILD=1 — dropping '$DEMO_DB'"
  drop_db "$DEMO_DB"
fi

# --- 1. platform DB ---------------------------------------------------------
if db_exists "$PLATFORM_DB" \
   && [ "$(psql_q "$PLATFORM_DB" "SELECT state FROM ir_module_module WHERE name='ncollection_saas'")" = "installed" ]; then
  echo "==> platform DB '$PLATFORM_DB' ready — skip"
else
  echo "==> creating platform DB '$PLATFORM_DB' (ncollection_saas) — takes a few minutes…"
  "${DC[@]}" exec -T odoo odoo -d "$PLATFORM_DB" -i ncollection_saas \
    --without-demo=True --no-http --stop-after-init "${DBARGS[@]}" --log-level=warn >/dev/null
fi

# --- 2. provision through the P2-T01 engine ---------------------------------
if db_exists "$DEMO_DB"; then
  echo "==> tenant '$DEMO_DB' already provisioned — skip (REBUILD=1 to recreate)"
else
  echo "==> provisioning '$DEMO_DB' via the provisioning engine…"
  "${DC[@]}" exec -T odoo odoo shell -d "$PLATFORM_DB" --no-http --log-level=warn "${DBARGS[@]}" >/dev/null <<PY
plan = env['ncollection.subscription.plan'].search([('code','=','DEMO')], limit=1)
if not plan:
    plan = env['ncollection.subscription.plan'].create({
        'name': 'Demo Plan', 'code': 'DEMO',
        'allowed_module_names': 'crm,sale,account', 'max_users': 25})
tenant = env['ncollection.tenant'].search([('database_name','=','${DEMO_DB}')], limit=1)
if not tenant:
    tenant = env['ncollection.tenant'].create({
        'company_name': 'Al Barari Trading', 'database_name': '${DEMO_DB}',
        'email': 'owner@albarari.ae', 'plan_id': plan.id, 'status': 'active'})
job = env['ncollection.provisioning.job'].create({
    'tenant_id': tenant.id, 'database_name': '${DEMO_DB}'})
env.cr.commit()
job.action_run_sync()
if job.status != 'done':
    raise SystemExit('provisioning failed: %s' % (job.log or '')[-400:])
env.cr.commit()
PY
  db_exists "$DEMO_DB" || { echo "ERROR: provisioning did not create '$DEMO_DB'" >&2; exit 1; }
fi

# --- 3. seed curated GCC data ----------------------------------------------
echo "==> seeding business data…"
"${DC[@]}" exec -T -e "DEMO_ADMIN_PASSWORD=$DEMO_PW" -e "SEED_FORCE=${SEED_FORCE:-0}" \
  odoo odoo shell -d "$DEMO_DB" --no-http --log-level=error "${DBARGS[@]}" \
  < scripts/demo/seed_demo_data.py 2>/dev/null | grep '^SEED:' || true

# Enforcement and menu visibility are @ormcache'd per process; restart so the
# freshly seeded roles and config are actually reflected.
echo "==> refreshing caches…"
odoo_cid="$("${DC[@]}" ps -q odoo)"
[ -n "$odoo_cid" ] || { echo "ERROR: odoo container not running." >&2; exit 1; }
docker restart "$odoo_cid" >/dev/null || { echo "ERROR: could not restart odoo." >&2; exit 1; }
ready=0
for _ in $(seq 1 40); do
  if docker exec "$odoo_cid" curl -sf http://localhost:8069/web/health >/dev/null 2>&1; then ready=1; break; fi
  sleep 2
done
[ "$ready" = 1 ] || { echo "ERROR: odoo did not become healthy after restart." >&2; exit 1; }

cat <<MSG

✅ Demo tenant ready.

   URL      http://${DEMO_DB}.localhost
   Owner    owner@albarari.ae / ${DEMO_PW}
   Staff    layla@ (Owner) · fatima@ (Accountant) · yousef@ (Sales) · aisha@ (Employee)
            …all @albarari.ae, same password

   Log in as different users to see the dashboard change per role.
MSG
