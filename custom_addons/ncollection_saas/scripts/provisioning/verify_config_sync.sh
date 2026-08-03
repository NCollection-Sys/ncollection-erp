#!/usr/bin/env bash
# shellcheck disable=SC2015
# ============================================================================
#  P2-T03 — end-to-end workspace config-sync proof (local; heavy — not CI)
# ============================================================================
#  Against a platform DB with ncollection_saas installed AND the odoo container
#  carrying NC_CONFIG_SYNC_KEY (see .env), proves:
#    1. provisioning seeds the scoped config-sync service account + API key,
#    2. a plan UPGRADE propagates the new module set into the tenant DB via the
#       json2/bearer sync (allowed_module_names + max_users updated),
#    3. SUSPENDING the tenant projects subscription_status='suspended',
#    4. the reconciliation cron HEALS a manually-broken tenant config.
#
#  The CI-safe unit tests (tests/test_config_sync.py, tests/test_subscription_
#  gate.py) cover the pure logic + the interstitial gate; THIS exercises the
#  real cross-DB json2 round-trip. Usage:  PLATFORM_DB=saastest bash <this>
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")/../../../.."   # repo root

PLATFORM_DB="${PLATFORM_DB:-saastest}"
TENANT_DB="provsync"
DC=(docker compose)
DBARGS=(--db_host=db --db_user=odoo --db_password=odoo)
pass=0; fail=0
ok(){ echo "  ✅ PASS: $1"; pass=$((pass + 1)); }
no(){ echo "  ❌ FAIL: $1"; fail=$((fail + 1)); }
hr(){ echo "----------------------------------------------------------------------"; }
tq(){ "${DC[@]}" exec -T db psql -U odoo -d "$1" -tAc "$2" 2>/dev/null | tr -d '[:space:]'; }

# --- keep the platform DB's schema in step with the code (#283) -------------
# PLATFORM_DB is PERSISTENT and was never upgraded here, so the first commit to
# add a model FIELD killed this suite with:
#     psycopg2.errors.UndefinedColumn: column "..." does not exist
# Routing passed, provisioning died on its first create, and the two suites
# after it never ran. A Rule 13 gate that cannot survive a schema change is a
# gate people learn to route around -- the failure mode #221/#264/#267 each
# fixed elsewhere. Fail LOUD (Rule 10): a swallowed upgrade would print the
# suite's own "ready" over a stale schema, which is R-005's exact shape.
platform_schema_sync(){
  echo "  syncing ${PLATFORM_DB} schema with the code (odoo -u ncollection_saas) ..."
  if ! "${DC[@]}" exec -T odoo odoo -d "$PLATFORM_DB" -u ncollection_saas \
       --stop-after-init --no-http --log-level=warn "${DBARGS[@]}" >/dev/null 2>&1; then
    echo "REFUSING: could not upgrade ncollection_saas on '$PLATFORM_DB'." >&2
    echo "  Running on a stale schema would report a code failure that is" >&2
    echo "  really a setup problem. Fix the upgrade, then re-run." >&2
    exit 1
  fi
}
platform_schema_sync
drop_db(){ "${DC[@]}" exec -T db psql -U odoo -d postgres -c \
  "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$1'" >/dev/null 2>&1 || true
  "${DC[@]}" exec -T db psql -U odoo -d postgres -c "DROP DATABASE IF EXISTS $1" >/dev/null 2>&1 || true; }

PLAN_CODE="SYNC$$"   # unique per run — plan.code is SQL-unique

echo "== cleanup any prior run (tenant DB + platform fixtures) =="
drop_db "$TENANT_DB"
"${DC[@]}" exec -T odoo odoo shell -d "$PLATFORM_DB" --no-http --log-level=error "${DBARGS[@]}" <<PY 2>/dev/null || true
try:
    env['ncollection.subscription'].search([('tenant_id.database_name','=','$TENANT_DB')]).unlink()
    env['ncollection.tenant'].search([('database_name','=','$TENANT_DB')]).unlink()
    env.cr.commit()
except Exception as e:
    print('cleanup skipped:', e)
PY

echo "== provision '$TENANT_DB' (plan $PLAN_CODE: crm) =="
"${DC[@]}" exec -T odoo odoo shell -d "$PLATFORM_DB" --no-http --log-level=warn "${DBARGS[@]}" <<PY 2>/dev/null
plan = env['ncollection.subscription.plan'].create({
    'name': 'Sync Starter', 'code': '$PLAN_CODE', 'allowed_module_names': 'crm', 'max_users': 3})
tenant = env['ncollection.tenant'].create({
    'company_name': 'Sync Co', 'database_name': '$TENANT_DB',
    'email': 'owner@sync.example', 'plan_id': plan.id, 'status': 'trial'})
sub = env['ncollection.subscription'].create({
    'name': 'SUB', 'tenant_id': tenant.id, 'plan_id': plan.id, 'status': 'draft'})
tenant.subscription_id = sub.id
env['ir.config_parameter'].sudo().set_param('ncollection_saas.provisioning_quota_per_hour', '0')
job = env['ncollection.provisioning.job'].create({'tenant_id': tenant.id, 'database_name': '$TENANT_DB'})
env.cr.commit()
job.action_run_sync()
env.cr.commit()
print('PROVISIONED', job.status, tenant.database_status)
PY
hr

echo "== service account seeded in the tenant DB =="
[ "$(tq "$TENANT_DB" "SELECT login FROM res_users WHERE login='config-sync@ncollection.internal'")" = "config-sync@ncollection.internal" ] \
  && ok "scoped config-sync service user created" || no "service user missing"
[ "$(tq "$TENANT_DB" "SELECT COUNT(*) FROM res_users_apikeys k JOIN res_users u ON u.id=k.user_id WHERE u.login='config-sync@ncollection.internal'")" = "1" ] \
  && ok "service-account bearer key provisioned (hash only)" || no "service key missing"
[ "$(tq "$TENANT_DB" "SELECT COUNT(*) FROM res_groups_users_rel r JOIN res_users u ON u.id=r.uid JOIN ir_model_data d ON d.model='res.groups' AND d.res_id=r.gid AND d.module='ncollection_core' AND d.name='group_config_sync' WHERE u.login='config-sync@ncollection.internal'")" = "1" ] \
  && ok "service user is in the least-privilege group_config_sync" || no "service user not scoped"
hr

echo "== plan UPGRADE propagates to the tenant workspace =="
"${DC[@]}" exec -T odoo odoo shell -d "$PLATFORM_DB" --no-http --log-level=error "${DBARGS[@]}" <<PY 2>/dev/null
t = env['ncollection.tenant'].search([('database_name','=','$TENANT_DB')], limit=1)
t.plan_id.write({'allowed_module_names': 'crm,sale,account', 'max_users': 20})
t.sync_workspace_config()
env.cr.commit()
print('SYNCED')
PY
[ "$(tq "$TENANT_DB" "SELECT allowed_module_names FROM ncollection_workspace_config LIMIT 1" | tr -d ',')" = "crmsaleaccount" ] \
  && ok "upgraded module set pushed (crm,sale,account)" || no "module set not propagated"
[ "$(tq "$TENANT_DB" "SELECT max_users FROM ncollection_workspace_config LIMIT 1")" = "20" ] \
  && ok "seat cap propagated (max_users=20)" || no "max_users not propagated"
hr

echo "== SUSPEND projects subscription_status='suspended' =="
"${DC[@]}" exec -T odoo odoo shell -d "$PLATFORM_DB" --no-http --log-level=error "${DBARGS[@]}" <<PY 2>/dev/null
t = env['ncollection.tenant'].search([('database_name','=','$TENANT_DB')], limit=1)
# provisioning already activated the tenant (P2-T02 _mark_done); suspend directly
t.action_suspend()    # active -> suspended
t.sync_workspace_config()
env.cr.commit()
print('SUSPENDED', t.status)
PY
[ "$(tq "$TENANT_DB" "SELECT subscription_status FROM ncollection_workspace_config LIMIT 1")" = "suspended" ] \
  && ok "suspension propagated to the tenant (interstitial trigger)" || no "suspension not propagated"
hr

echo "== reconciliation cron HEALS a manually broken config =="
# tamper: wipe the tenant's allowed modules directly in its DB
"${DC[@]}" exec -T db psql -U odoo -d "$TENANT_DB" -c \
  "UPDATE ncollection_workspace_config SET allowed_module_names='TAMPERED'" >/dev/null 2>&1
"${DC[@]}" exec -T odoo odoo shell -d "$PLATFORM_DB" --no-http --log-level=error "${DBARGS[@]}" <<'PY' 2>/dev/null
env['ncollection.tenant'].sync_workspace_config  # ensure model loaded
env['ncollection.tenant']._cron_reconcile_config()
# the cron enqueues; run the enqueued syncs inline for the proof
for t in env['ncollection.tenant'].search([('database_status','=','ready')]):
    t.sync_workspace_config()
env.cr.commit()
print('RECONCILED')
PY
[ "$(tq "$TENANT_DB" "SELECT allowed_module_names FROM ncollection_workspace_config LIMIT 1" | tr -d ',')" = "crmsaleaccount" ] \
  && ok "reconcile healed the tampered config" || no "reconcile did NOT heal"
hr

echo "== cleanup =="
drop_db "$TENANT_DB"

echo "SUMMARY: $pass passed, $fail failed."
[ "$fail" -eq 0 ] && echo "✅ Config sync verified (provision + propagate + suspend + reconcile)." \
  || { echo "❌ FAILURES above."; exit 1; }
