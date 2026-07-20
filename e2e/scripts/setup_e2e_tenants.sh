#!/usr/bin/env bash
# ============================================================================
#  P1-T20 — create the E2E test tenants on the running routing stack
# ============================================================================
#  Two tenants on DIFFERENT plans (so visibility + license journeys diverge)
#  plus the admin platform DB. Idempotent. Requires the routing overlay up
#  (db_filter ON): `make routing-up`. Reuses the P1-T06 setup pattern.
#
#  Both install the SAME modules (crm + sale); they differ only in the licensed
#  set (allowed_module_names) — so clientb has `sale` INSTALLED but UNLICENSED,
#  which is exactly what P1-T09 (menu hidden) and P1-T10 (access blocked) enforce.
#    clienta  = Pro   plan: allowed = "crm,sale"   (Sales visible + usable)
#    clientb  = Basic plan: allowed = "crm"         (Sales installed, hidden+blocked)
#    admin    = platform DB (routing target)
#
#  Admin login for every tenant: admin / $E2E_ADMIN_PW (default "admin").
#  clienta also gets role users:  sales@clienta / acct@clienta  (pw demo1234).
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")/../.."   # repo root

DC=(docker compose)
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
create_tenant clienta "base,ncollection_core,ncollection_branding,crm,sale" "crm,sale"
create_tenant clientb "base,ncollection_core,ncollection_branding,crm,sale" "crm"

# admin platform DB (minimal — a routing target for the admin.localhost journey).
if db_exists admin; then
  echo "  • admin exists — skip create"
else
  echo "  • creating admin (base)…"
  "${DC[@]}" exec -T odoo odoo -d admin -i base --without-demo=True --no-http \
    --stop-after-init "${DBARGS[@]}" >/dev/null 2>&1
fi

# A non-system "business" user with the standard Sales groups on BOTH tenants
# (login: biz / demo1234). Enforcement is bypassed for system users (the
# Owner/admin), so the journeys probe as `biz`: it HAS the Sales groups, so
# Sales is gated purely by the plan license — visible on clienta (licensed),
# hidden + access-denied on clientb (unlicensed). Owner-only menus (Settings)
# stay hidden from `biz` and visible to admin (the role/owner spot check).
echo "  • seeding business user 'biz' (Sales groups, non-system) on both tenants…"
for t in clienta clientb; do
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
echo "  • refreshing caches (restart odoo + nginx)…"
docker restart ncollection-odoo >/dev/null 2>&1 || true
for _ in $(seq 1 30); do
  docker exec ncollection-odoo curl -sf http://localhost:8069/web/health >/dev/null 2>&1 && break
  sleep 2
done
docker restart ncollection-nginx >/dev/null 2>&1 || true
sleep 3

echo "✅ E2E tenants ready: clienta (Pro) · clientb (Basic) · admin"
