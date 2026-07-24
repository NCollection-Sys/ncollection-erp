#!/usr/bin/env bash
# ============================================================================
#  P1-T20 — create the E2E test tenants on the running routing stack
# ============================================================================
#  Two tenants on DIFFERENT plans (so visibility + license journeys diverge)
#  plus the admin platform DB. Idempotent. Requires the routing overlay up
#  (db_filter ON): `make routing-up`. Reuses the P1-T06 setup pattern.
#
#  Both install the SAME modules (crm + sale); they differ only in the licensed
#  set (allowed_module_names) — so e2eclientb has `sale` INSTALLED but UNLICENSED,
#  which is exactly what P1-T09 (menu hidden) and P1-T10 (access blocked) enforce.
#    e2eclienta = Pro   plan: allowed = "crm,sale"  (Sales visible + usable)
#    e2eclientb = Basic plan: allowed = "crm"       (Sales installed, hidden+blocked)
#    e2eadmin   = platform DB (routing target)
#
#  FIXTURE NAMESPACE — these names are deliberately prefixed `e2e*`. The P1-T06
#  routing proof owns rtclienta/rtclientb/rtadmin and `make routing-clean` drops those.
#  Sharing one namespace across two suites meant either could destroy the other's
#  fixtures; the prefix makes that structurally impossible. The names must stay
#  ALPHANUMERIC: db_filter=^%d$ maps a subdomain to the DB of the same name, and
#  underscores are invalid in hostnames while hyphens need Postgres quoting.
#
#  Admin login for every tenant: admin / $E2E_ADMIN_PW (default "admin").
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")/../.."   # repo root

# This suite requires the ROUTING stack: base (db+odoo) + dev (the nginx edge)
# + routing (db_filter=^%d$). `docker compose` alone loads ONLY the base file,
# which does not define `nginx` — so service lookups like `ps -q nginx` fail.
# Honour a caller-supplied COMPOSE_FILE (CI sets it); otherwise default to the
# exact trio `make routing-up` starts.
if [ -n "${COMPOSE_FILE:-}" ]; then
  DC=(docker compose)
else
  DC=(docker compose -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.routing.yml)
fi
DBARGS=(--db_host=db --db_user=odoo --db_password=odoo)
ADMIN_PW="${E2E_ADMIN_PW:-admin}"

db_exists(){ "${DC[@]}" exec -T db psql -U odoo -d postgres -tAc \
  "SELECT 1 FROM pg_database WHERE datname='$1'" 2>/dev/null | grep -q 1; }
# "provisioned" = the tenant modules are actually installed (guards against a
# stale base-only DB left by another test, e.g. P1-T06 routing).
tenant_provisioned(){ "${DC[@]}" exec -T db psql -U odoo -d "$1" -tAc \
  "SELECT 1 FROM ir_module_module WHERE name='ncollection_core' AND state='installed'" \
  2>/dev/null | grep -q 1; }
drop_db(){ "${DC[@]}" exec -T db psql -U odoo -d postgres -c \
  "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$1'" >/dev/null 2>&1 || true
  "${DC[@]}" exec -T db psql -U odoo -d postgres -c "DROP DATABASE IF EXISTS $1" >/dev/null 2>&1 || true; }

# create_tenant <db> <modules> <allowed_module_names>
create_tenant(){
  local db="$1" modules="$2" allowed="$3"
  if tenant_provisioned "$db"; then
    echo "  • $db already provisioned — skip create"
  else
    echo "  • (re)creating $db (modules: $modules) — this takes a minute…"
    drop_db "$db"
    "${DC[@]}" exec -T odoo odoo -d "$db" -i "$modules" --without-demo=True --no-http \
      --stop-after-init "${DBARGS[@]}" >/dev/null 2>&1
  fi
  # Deterministic seed: known admin creds + the plan's allowed-module projection.
  "${DC[@]}" exec -T odoo odoo shell -d "$db" --no-http --log-level=error "${DBARGS[@]}" \
    >/dev/null 2>&1 <<PY
admin = env.ref('base.user_admin')
admin.write({'login': 'admin', 'password': '${ADMIN_PW}'})
Cfg = env['ncollection.workspace.config']
cfg = Cfg.search([], limit=1)
vals = {'allowed_module_names': '''${allowed}'''}
cfg.write(vals) if cfg else Cfg.create(vals)
env.cr.commit()
PY
  echo "  • $db seeded (admin/${ADMIN_PW}; allowed='${allowed}')"
}

echo "Setting up E2E tenants…"
create_tenant e2eclienta "base,ncollection_core,ncollection_branding,crm,sale" "crm,sale"
create_tenant e2eclientb "base,ncollection_core,ncollection_branding,crm,sale" "crm"

# platform DB — the SaaS platform stack (checkout routes + plans) so the
# e2eadmin.localhost public-checkout journey (P2-T18/T16) has real endpoints.
if ! db_exists e2eadmin; then
  echo "  • creating e2eadmin (base)…"
  "${DC[@]}" exec -T odoo odoo -d e2eadmin -i base --without-demo=True --no-http \
    --stop-after-init "${DBARGS[@]}" >/dev/null 2>&1
fi
if ! "${DC[@]}" exec -T db psql -U odoo -d e2eadmin -tAc \
     "SELECT 1 FROM ir_module_module WHERE name='ncollection_saas' AND state='installed'" \
     2>/dev/null | grep -q 1; then
  echo "  • installing platform stack (ncollection_saas) on e2eadmin…"
  "${DC[@]}" exec -T odoo odoo -d e2eadmin -i ncollection_saas --without-demo=True --no-http \
    --stop-after-init "${DBARGS[@]}" >/dev/null 2>&1
fi
# Deterministic admin creds + a checkout plan (write-or-create) for the register
# journey. `odoo shell` is a REPL: an exception in the piped script does NOT set a
# non-zero exit, so `set -e` can't see a failed Plan.create()/missing ref. Print a
# sentinel on success and grep for it — a silent seed failure would otherwise
# surface downstream as a confusing invalid_plan/quota_exceeded test error instead
# of a clear setup failure (fail loud, Rule 10). Write-or-create keeps an already
# seeded e2eadmin's plan values current, matching create_tenant's config seed above.
seed_out="$("${DC[@]}" exec -T odoo odoo shell -d e2eadmin --no-http --log-level=error "${DBARGS[@]}" 2>&1 <<PY
env.ref('base.user_admin').write({'login': 'admin', 'password': '${ADMIN_PW}'})
Plan = env['ncollection.subscription.plan']
vals = {'name': 'E2E Starter', 'code': 'E2ESTARTER', 'allowed_module_names': 'crm', 'max_users': 3}
plan = Plan.search([('code', '=', 'E2ESTARTER')], limit=1)
plan.write(vals) if plan else Plan.create(vals)
# Purge stale checkout trial tenants so repeated local runs stay under the per-IP
# trial quota (3/24h) — a leaked trial from an interrupted run would otherwise make
# the register journey fail with quota_exceeded (#214-E). CI is unaffected (fresh db).
Tenant = env['ncollection.tenant'].sudo()
trials = Tenant.search([('status', '=', 'trial'), ('checkout_source_ip', '!=', False)])
# subscriptions first: subscription.tenant_id is ondelete=restrict, so a tenant
# cannot be unlinked while a subscription still points at it.
env['ncollection.subscription'].sudo().search([('tenant_id', 'in', trials.ids)]).unlink()
trials.unlink()
env.cr.commit()
print('E2EADMIN_SEED_OK')
PY
)"
echo "$seed_out" | grep -q 'E2EADMIN_SEED_OK' || {
  echo "ERROR: e2eadmin seed (admin creds + E2ESTARTER plan) failed — checkout journeys depend on it:" >&2
  echo "$seed_out" >&2
  exit 1; }

# A non-system "business" user with the standard Sales groups on BOTH tenants
# (login: biz / demo1234). Enforcement is bypassed for system users (the
# Owner/admin), so the journeys probe as `biz`: it HAS the Sales groups, so
# Sales is gated purely by the plan license — visible on e2eclienta (licensed),
# hidden + access-denied on e2eclientb (unlicensed). Owner-only menus (Settings)
# stay hidden from `biz` and visible to admin (the role/owner spot check).
echo "  • seeding business user 'biz' (Sales groups, non-system) on both tenants…"
for t in e2eclienta e2eclientb; do
  "${DC[@]}" exec -T odoo odoo shell -d "$t" --no-http --log-level=error "${DBARGS[@]}" \
    >/dev/null 2>&1 <<'PY'
Users = env['res.users']
groups = [env.ref('base.group_user').id]
sale_grp = env.ref('sales_team.group_sale_salesman', raise_if_not_found=False)
if sale_grp:
    groups.append(sale_grp.id)
u = Users.search([('login', '=', 'biz')], limit=1)
if u:
    u.write({'password': 'demo1234', 'group_ids': [(6, 0, groups)]})
else:
    Users.create({'name': 'biz', 'login': 'biz', 'password': 'demo1234',
                  'group_ids': [(6, 0, groups)]})
env.cr.commit()
PY
done

# Refresh the live server's caches so it reflects the new tenants/config/users.
# License enforcement + menu visibility are @ormcache'd per process; the config
# and users were written from separate `odoo shell` processes, so restart the
# server (and reload nginx onto odoo's fresh IP) to guarantee a clean state.
#
# These restarts are LOAD-BEARING: if one silently fails, the suite runs against a
# stale @ormcache and reports confusing results. So they FAIL LOUD — never `|| true`
# on a step whose success we depend on. Container IDs are derived from compose
# rather than hardcoded, so a non-default COMPOSE_PROJECT_NAME still works.
echo "  • refreshing caches (restart odoo + nginx)…"

cid(){ "${DC[@]}" ps -q "$1"; }

odoo_cid="$(cid odoo)"
nginx_cid="$(cid nginx)"
[ -n "$odoo_cid" ] || {
  echo "ERROR: no running 'odoo' service container. Start the stack first: make routing-up" >&2; exit 1; }
[ -n "$nginx_cid" ] || {
  echo "ERROR: no running 'nginx' service container. Start the stack first: make routing-up" >&2; exit 1; }

docker restart "$odoo_cid" >/dev/null || {
  echo "ERROR: failed to restart odoo ($odoo_cid) — enforcement caches would be stale." >&2; exit 1; }

odoo_ready=0
for _ in $(seq 1 30); do
  if docker exec "$odoo_cid" curl -sf http://localhost:8069/web/health >/dev/null 2>&1; then
    odoo_ready=1; break
  fi
  sleep 2
done
[ "$odoo_ready" = 1 ] || {
  echo "ERROR: odoo did not become healthy within 60s of restart — aborting." >&2
  "${DC[@]}" logs --tail=50 odoo >&2
  exit 1; }

docker restart "$nginx_cid" >/dev/null || {
  echo "ERROR: failed to restart nginx ($nginx_cid) — it may hold a stale odoo IP." >&2; exit 1; }

# Deterministic edge readiness (no blind sleep): any HTTP answer on :80 means
# nginx is serving again. Tenant-agnostic on purpose.
edge_ready=0
for _ in $(seq 1 15); do
  if curl -s -o /dev/null http://localhost/ 2>/dev/null; then edge_ready=1; break; fi
  sleep 1
done
[ "$edge_ready" = 1 ] || {
  echo "ERROR: the nginx edge did not answer on :80 within 15s of restart." >&2; exit 1; }

echo "✅ E2E tenants ready: e2eclienta (Pro) · e2eclientb (Basic) · e2eadmin"
