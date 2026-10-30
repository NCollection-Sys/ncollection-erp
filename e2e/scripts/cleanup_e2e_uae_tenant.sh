#!/usr/bin/env bash
# ============================================================================
#  Remove the `e2euae` fixture (setup_e2e_uae_tenant.sh) — and NOTHING else.
# ============================================================================
#  Fail closed. Before anything is removed, every one of these must hold, or the script stops having changed nothing:
#    1. the database name is exactly `e2euae` and the platform is exactly `e2eadmin` (hard-coded, not parameters);
#    2. the platform has exactly ONE tenant for it, with database_name = subdomain = e2euae, and the fixture marker in
#       contact_name;
#    3. that tenant is on the plan E2EUAE, whose name carries the same marker, and no OTHER tenant uses that plan;
#    4. the database, when it exists, carries the same marker in ir.config_parameter `ncollection.e2e.fixture`
#       (written by the setup script) — a company display name is never treated as proof of ownership;
#    5. nothing that is not this fixture is named: the names below are compared with `=`, never a pattern.
#  Order: the tenant database and its filestore first, then the platform rows (queued jobs, override, provisioning
#  jobs, tenant, plan), so a failure part-way never leaves a platform record pointing at a database that is gone
#  without saying so. Re-running after a partial run is safe: each guard accepts "already gone".
# ============================================================================
set -euo pipefail

cd "$(dirname "$0")/../.."   # repo root

PLATFORM_DB=e2eadmin
TENANT_DB=e2euae
PLAN_CODE=E2EUAE
FIXTURE_MARK="ncollection-e2e-fixture:${TENANT_DB}@${PLATFORM_DB}"
PROTECTED=" e2eclienta e2eclientb e2eadmin ncollection saastest uaetest ncplatform postgres template0 template1 "

DC=(docker compose)
DBARGS=(--db_host=db --db_user=odoo --db_password=odoo)

psql_db(){ "${DC[@]}" exec -T db psql -U odoo -d "$1" -tAc "$2"; }
refuse(){ echo "❌ REFUSING (nothing removed): $*" >&2; exit 1; }

case "$PROTECTED" in *" $TENANT_DB "*) refuse "'$TENANT_DB' is a protected database" ;; esac
[ "$TENANT_DB" = e2euae ] && [ "$PLATFORM_DB" = e2eadmin ] || refuse "identities were edited"

# --- 2 + 3: the platform side, read-only first ------------------------------------------------------------------
check=$("${DC[@]}" exec -T -e NC_E2E_TENANT="$TENANT_DB" -e NC_E2E_PLAN="$PLAN_CODE" -e NC_E2E_MARK="$FIXTURE_MARK" \
          odoo odoo shell -d "$PLATFORM_DB" --no-http --log-level=warn "${DBARGS[@]}" 2>&1 <<'PY'
import os
tenant_db, plan_code, mark = os.environ['NC_E2E_TENANT'], os.environ['NC_E2E_PLAN'], os.environ['NC_E2E_MARK']
Tenant = env['ncollection.tenant'].with_context(active_test=False)
Plan = env['ncollection.subscription.plan'].with_context(active_test=False)
tenants = Tenant.search(['|', ('database_name', '=', tenant_db), ('subdomain', '=', tenant_db)])
plans = Plan.search([('code', '=', plan_code)])
problems = []
if len(tenants) > 1:
    problems.append('%d tenants answer to %s' % (len(tenants), tenant_db))
for t in tenants:
    if t.database_name != tenant_db or t.subdomain != tenant_db or t.contact_name != mark:
        problems.append('tenant %s is not this fixture (no marker / other names)' % t.id)
if len(plans) > 1:
    problems.append('%d plans with code %s' % (len(plans), plan_code))
for p in plans:
    if mark not in (p.name or ''):
        problems.append('plan %s has no fixture marker' % p.id)
    others = Tenant.search([('plan_id', '=', p.id), ('id', 'not in', tenants.ids)])
    if others:
        problems.append('plan %s is also used by tenants %s' % (p.id, others.ids))
if tenants and plans and tenants.plan_id != plans:
    problems.append('the tenant is not on the fixture plan')
print('PROBLEMS=' + ' | '.join(problems) if problems else 'VERIFIED tenants=%s plans=%s' % (tenants.ids, plans.ids))
PY
) || refuse "the platform check could not run: $(tail -5 <<<"$check")"
grep -q '^VERIFIED' <<<"$check" || refuse "$(grep -E '^PROBLEMS=' <<<"$check" || tail -5 <<<"$check")"
echo "  platform: $(grep '^VERIFIED' <<<"$check")"

# --- 4: the database side ---------------------------------------------------------------------------------------
if [ "$(psql_db postgres "SELECT 1 FROM pg_database WHERE datname='$TENANT_DB'")" = 1 ]; then
  mark="$(psql_db "$TENANT_DB" "SELECT value FROM ir_config_parameter WHERE key='ncollection.e2e.fixture'")" \
    || refuse "cannot read the ownership marker of '$TENANT_DB'"
  [ "$mark" = "$FIXTURE_MARK" ] || refuse "'$TENANT_DB' does not carry this fixture's ownership marker"
  echo "  database: marker verified — dropping '$TENANT_DB' and its filestore"
  psql_db postgres "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$TENANT_DB'" >/dev/null
  psql_db postgres "DROP DATABASE \"$TENANT_DB\"" >/dev/null
  MSYS_NO_PATHCONV=1 "${DC[@]}" exec -T odoo rm -rf -- "/var/lib/odoo/filestore/$TENANT_DB"
else
  echo "  database: '$TENANT_DB' does not exist (nothing to drop)"
fi

# --- the platform rows, only those verified above ---------------------------------------------------------------
out=$("${DC[@]}" exec -T -e NC_E2E_TENANT="$TENANT_DB" -e NC_E2E_PLAN="$PLAN_CODE" -e NC_E2E_MARK="$FIXTURE_MARK" \
  odoo odoo shell -d "$PLATFORM_DB" --no-http --log-level=warn "${DBARGS[@]}" 2>&1 <<'PY'
import os
tenant_db, plan_code, mark = os.environ['NC_E2E_TENANT'], os.environ['NC_E2E_PLAN'], os.environ['NC_E2E_MARK']
tenant = env['ncollection.tenant'].with_context(active_test=False).search(
    [('database_name', '=', tenant_db), ('subdomain', '=', tenant_db), ('contact_name', '=', mark)])
plan = env['ncollection.subscription.plan'].with_context(active_test=False).search([('code', '=', plan_code)])
plan = plan.filtered(lambda p: mark in (p.name or ''))
if tenant:
    # jobs this fixture enqueued: e2eadmin has no runner, so they would otherwise sit pending forever
    jobs = env['queue.job'].search([('model_name', '=', 'ncollection.tenant'), ('state', '!=', 'done')]).filtered(
        lambda j: j.records.ids == tenant.ids)
    jobs.unlink()
    env['ncollection.tenant.module.override'].with_context(active_test=False).search(
        [('tenant_id', '=', tenant.id)]).unlink()
    env['ncollection.provisioning.job'].search([('tenant_id', '=', tenant.id)]).unlink()
    tenant.unlink()
    print('REMOVED tenant + %d queued jobs' % len(jobs))
if plan:
    plan.unlink()
    print('REMOVED plan')
env.cr.commit()
print('STEP_OK cleanup')
PY
) || { echo "❌ removing the platform rows failed (database already dropped; re-run to finish):" >&2; tail -30 <<<"$out" >&2; exit 1; }
grep -q '^STEP_OK' <<<"$out" || { echo "❌ platform cleanup did not complete:" >&2; tail -30 <<<"$out" >&2; exit 1; }
grep -E '^REMOVED' <<<"$out" | sed 's/^/  platform: /' || true
echo "✅ e2euae fixture removed"
