#!/usr/bin/env bash
# ============================================================================
#  UAE e-invoicing E2E fixture: the `e2euae` tenant, provisioned THROUGH the platform registered in `e2eadmin`.
# ============================================================================
#  Why a tenant of its own: ncollection_uae_einvoicing is a GATED module (ncollection_subscription/models/
#  gated_modules.py). It is eligible only for a tenant whose country is AE AND whose books are VERIFIED to be on the
#  AE chart in AED, and it is activated only by an explicit per-tenant `enable` override. e2eclienta / e2eclientb are
#  US tenants on the generic chart with posted entries, and they are not in any platform registry, so they can never be
#  eligible; converting them would break the specs that assume them. `e2euae` is created instead and nothing else is
#  touched.
#
#  The path is the platform's own, synchronously (the queue_job runner serves only `ncollection`, never e2eadmin):
#    plan E2EUAE (account) -> tenant (country AE) -> provisioning job .action_run_sync()   [installs the AE package and
#    verifies ae/AED itself] -> .action_apply_localization() [records the verified books] -> override `enable`
#    ncollection_uae_einvoicing [refused unless eligible] -> .action_install_licensed_modules_sync() ->
#    .sync_workspace_config() [the licence the menus read].
#  Then, inside e2euae: the fixture-ownership marker, the e-invoicing manager group for the owner, and `biz` (no
#  e-invoicing group) for the access-denied test. The A-Cube adapter is never enabled and no provider or credential is
#  created, so nothing can reach an ASP.
#
#  Idempotent and resumable: every step checks what exists first, so a second run changes nothing and an interrupted
#  run continues. The tenant database gets its ownership marker straight after provisioning, once the platform proves
#  it created it (mark_tenant_db); an unmarked database is never trusted by its name alone.
#  Removal: e2e/scripts/cleanup_e2e_uae_tenant.sh (`make e2e-uae-clean`), which verifies ownership before dropping.
#  Dev-only fixture credentials (never prod): owner `e2e.owner` / $E2E_UAE_OWNER_PW, `biz` / demo1234.
# ============================================================================
set -euo pipefail

cd "$(dirname "$0")/../.."   # repo root

# --- identities: hard-coded, never taken from the environment ---------------------------------------------------
PLATFORM_DB=e2eadmin
TENANT_DB=e2euae
PLAN_CODE=E2EUAE
FIXTURE_MARK="ncollection-e2e-fixture:${TENANT_DB}@${PLATFORM_DB}"
OWNER_PW="${E2E_UAE_OWNER_PW:-E2e-Uae-Owner-77!}"

DC=(docker compose)
DBARGS=(--db_host=db --db_user=odoo --db_password=odoo)
LOG_DIR="$(mktemp -d)"
trap 'rm -rf "$LOG_DIR"' EXIT

psql_db(){ "${DC[@]}" exec -T db psql -U odoo -d "$1" -tAc "$2"; }

refuse(){ echo "❌ REFUSING: $*" >&2; exit 1; }

# A platform shell step: stdin is Python, the result must end with the marker STEP_OK, otherwise the whole output is
# shown and the run stops (no `|| true`, Rule 10).
platform_step(){
  local name="$1" out
  if ! out=$("${DC[@]}" exec -T -e NC_E2E_TENANT="$TENANT_DB" -e NC_E2E_PLAN="$PLAN_CODE" \
               -e NC_E2E_MARK="$FIXTURE_MARK" -e NC_E2E_OWNER_PW="$OWNER_PW" \
               odoo odoo shell -d "$PLATFORM_DB" --no-http --log-level=warn "${DBARGS[@]}" 2>&1); then
    echo "❌ $name failed:" >&2; echo "$out" | tail -40 >&2; exit 1
  fi
  echo "$out" > "$LOG_DIR/$name.log"
  if ! grep -q '^STEP_OK' <<<"$out"; then
    echo "❌ $name did not complete:" >&2; echo "$out" | tail -40 >&2; exit 1
  fi
  grep -E '^(STEP_OK|INFO)' <<<"$out" | sed "s/^/  [$name] /"
}

echo "== preflight =="
./scripts/dev/assert_oca_present.sh "the e2euae fixture"
[ "$(psql_db postgres "SELECT 1 FROM pg_database WHERE datname='$PLATFORM_DB'")" = 1 ] \
  || refuse "platform database '$PLATFORM_DB' does not exist (run e2e/scripts/setup_e2e_tenants.sh first)"
[ "$(psql_db "$PLATFORM_DB" "SELECT count(*) FROM ir_module_module WHERE state='installed' AND name IN ('ncollection_saas','ncollection_subscription')")" = 2 ] \
  || refuse "'$PLATFORM_DB' does not carry ncollection_saas + ncollection_subscription"
# An existing e2euae database must be OURS. A DIFFERENT marker stops here. NO marker is never trusted by name: it is
# only adopted by mark_tenant_db below, after the platform proves it created this database (an interrupted run).
tenant_marker(){ psql_db "$TENANT_DB" "SELECT coalesce(max(value),'') FROM ir_config_parameter WHERE key='ncollection.e2e.fixture'"; }
if [ "$(psql_db postgres "SELECT 1 FROM pg_database WHERE datname='$TENANT_DB'")" = 1 ]; then
  mark="$(tenant_marker)" || refuse "cannot read the ownership marker of '$TENANT_DB'"
  case "$mark" in
    "$FIXTURE_MARK") echo "  database '$TENANT_DB' carries this fixture's marker" ;;
    "") echo "  database '$TENANT_DB' has NO marker: it is adopted only if the platform proves it provisioned it" ;;
    *) refuse "database '$TENANT_DB' carries another ownership marker: $mark" ;;
  esac
fi

# Write (or confirm) the ownership marker in the tenant database. Allowed only when the platform shows exactly one
# fixture tenant for it, on the fixture plan, with a DONE provisioning job and a READY database — the proof that this
# script created it — and the database holds that tenant's company and owner login. Anything else refuses.
mark_tenant_db(){
  platform_step identity <<'PY'
import os
tenant_db, plan_code, mark = os.environ['NC_E2E_TENANT'], os.environ['NC_E2E_PLAN'], os.environ['NC_E2E_MARK']
tenants = env['ncollection.tenant'].with_context(active_test=False).search(
    ['|', ('database_name', '=', tenant_db), ('subdomain', '=', tenant_db)])
if len(tenants) != 1:
    raise SystemExit('REFUSE: %d platform tenants answer to %s' % (len(tenants), tenant_db))
t = tenants
if (t.database_name, t.subdomain, t.contact_name) != (tenant_db, tenant_db, mark):
    raise SystemExit('REFUSE: tenant %s is not this fixture' % t.id)
if t.plan_id.code != plan_code or mark not in (t.plan_id.name or ''):
    raise SystemExit('REFUSE: tenant %s is not on the fixture plan' % t.id)
if t.database_status != 'ready':
    raise SystemExit('REFUSE: tenant %s database is %s' % (t.id, t.database_status))
if not env['ncollection.provisioning.job'].search_count([('tenant_id', '=', t.id), ('database_name', '=', tenant_db),
                                                         ('status', '=', 'done')]):
    raise SystemExit('REFUSE: no done provisioning job created %s' % tenant_db)
print('INFO platform proves tenant', t.id, 'company', t.company_name, 'owner', t.admin_login)
print('STEP_OK identity')
PY
  local out
  if ! out=$("${DC[@]}" exec -T -e NC_E2E_MARK="$FIXTURE_MARK" odoo odoo shell -d "$TENANT_DB" --no-http \
               --log-level=warn "${DBARGS[@]}" 2>&1 <<'PY'
import os
mark = os.environ['NC_E2E_MARK']
ICP = env['ir.config_parameter'].sudo()
current = ICP.get_param('ncollection.e2e.fixture')
if current and current != mark:
    raise SystemExit('REFUSE: another fixture marker: %s' % current)
if env['res.company'].search_count([]) != 1 or env.company.name != 'E2E UAE CO':
    raise SystemExit('REFUSE: the database does not hold exactly the fixture company')
if not env['res.users'].with_context(active_test=False).search_count([('login', '=', 'e2e.owner')]):
    raise SystemExit('REFUSE: the provisioned owner e2e.owner is missing')
if current == mark:
    print('INFO marker unchanged')
else:
    ICP.set_param('ncollection.e2e.fixture', mark)
    env.cr.commit()
    print('INFO marker written')
print('STEP_OK marker')
PY
); then
    echo "❌ marker step failed:" >&2; echo "$out" | tail -40 >&2; exit 1
  fi
  grep -q '^STEP_OK' <<<"$out" || { echo "❌ marker step did not complete:" >&2; echo "$out" | tail -40 >&2; exit 1; }
  grep -E '^(STEP_OK|INFO)' <<<"$out" | sed 's/^/  [marker] /'
  [ "$(tenant_marker)" = "$FIXTURE_MARK" ] || refuse "the marker read back from '$TENANT_DB' does not match"
}

echo "== platform: plan, tenant, provisioning, localization =="
platform_step platform <<'PY'
import os
tenant_db, plan_code, mark = os.environ['NC_E2E_TENANT'], os.environ['NC_E2E_PLAN'], os.environ['NC_E2E_MARK']
Tenant = env['ncollection.tenant'].with_context(active_test=False)
Plan = env['ncollection.subscription.plan'].with_context(active_test=False)

# the plan: only ever ours (same code AND our marker in its name), never another plan reused
plan = Plan.search([('code', '=', plan_code)])
if len(plan) > 1 or (plan and mark not in (plan.name or '')):
    raise SystemExit('REFUSE: plan %s exists and is not this fixture\'s' % plan_code)
if not plan:
    plan = Plan.create({'name': 'E2E UAE plan (%s)' % mark, 'code': plan_code,
                        'allowed_module_names': 'account', 'max_users': 5})
    print('INFO plan created', plan.id)
else:
    print('INFO plan unchanged', plan.id)

# the tenant: identified by database name AND subdomain AND the marker in contact_name
tenant = Tenant.search(['|', ('database_name', '=', tenant_db), ('subdomain', '=', tenant_db)])
if len(tenant) > 1 or (tenant and (tenant.contact_name != mark or tenant.database_name != tenant_db
                                   or tenant.plan_id != plan)):
    raise SystemExit('REFUSE: a tenant for %s exists and is not this fixture\'s' % tenant_db)
ae = env.ref('base.ae')
if not tenant:
    tenant = Tenant.create({
        'company_name': 'E2E UAE CO', 'database_name': tenant_db, 'subdomain': tenant_db,
        'contact_name': mark, 'email': 'e2e.owner@e2euae.example', 'admin_login': 'e2e.owner',
        'admin_password': os.environ['NC_E2E_OWNER_PW'], 'admin_password_confirm': os.environ['NC_E2E_OWNER_PW'],
        'plan_id': plan.id, 'country_id': ae.id, 'status': 'trial'})
    print('INFO tenant created', tenant.id)
else:
    print('INFO tenant unchanged', tenant.id)
if tenant.country_id != ae:
    raise SystemExit('REFUSE: tenant %s is not AE' % tenant_db)
env.cr.commit()

# provisioning: the existing job, run inline
if tenant.database_status != 'ready':
    Job = env['ncollection.provisioning.job']
    job = Job.search([('tenant_id', '=', tenant.id), ('status', 'not in', ('done', 'failed'))], limit=1) \
        or Job.create({'tenant_id': tenant.id, 'database_name': tenant_db})
    env.cr.commit()
    job.action_run_sync()
    env.cr.commit()
    tenant.invalidate_recordset()
    if tenant.database_status != 'ready':
        raise SystemExit('FAIL provisioning job=%s db=%s\n%s' % (job.status, tenant.database_status, (job.log or '')[-2000:]))
    print('INFO provisioned', job.status)
else:
    print('INFO database unchanged (ready)')

# the books: the platform's own tenant-side check, then the fact gating reads
if not tenant._nc_books_verified():
    tenant.action_apply_localization()
    env.cr.commit()
    tenant.invalidate_recordset()
if not tenant._nc_books_verified():
    raise SystemExit('FAIL books not verified: %s %s' % (tenant.localization_applied_state,
                                                         tenant.localization_applied_message))
print('INFO books verified', tenant.localization_applied_message.strip().splitlines()[-1][:80])
print('STEP_OK platform')
PY

echo "== tenant ownership marker (written as soon as the platform proves the database is ours) =="
mark_tenant_db

echo "== platform: override, install, licence =="
platform_step entitlement <<'PY'
import os
tenant_db, plan_code, mark = os.environ['NC_E2E_TENANT'], os.environ['NC_E2E_PLAN'], os.environ['NC_E2E_MARK']
tenant = env['ncollection.tenant'].with_context(active_test=False).search(
    [('database_name', '=', tenant_db), ('subdomain', '=', tenant_db), ('contact_name', '=', mark)])
if len(tenant) != 1 or tenant.plan_id.code != plan_code or not tenant._nc_books_verified():
    raise SystemExit('REFUSE: the fixture tenant is not in the verified state the previous step left')

# the explicit decision: one enable override for the engine only (the A-Cube adapter is never added)
Override = env['ncollection.tenant.module.override'].with_context(active_test=False)
ov = Override.search([('tenant_id', '=', tenant.id), ('module_name', '=', 'ncollection_uae_einvoicing')])
if ov and (ov.mode != 'enable' or not ov.active):
    raise SystemExit('REFUSE: an override for the engine exists and is not an active enable')
if not ov:
    Override.create({'tenant_id': tenant.id, 'module_name': 'ncollection_uae_einvoicing', 'mode': 'enable',
                     'reason': 'E2E fixture (%s)' % mark})
    env.cr.commit()
    print('INFO override created')
else:
    print('INFO override unchanged')
if Override.search_count([('tenant_id', '=', tenant.id), ('module_name', '=', 'ncollection_uae_einvoicing_acube'),
                          ('mode', '=', 'enable'), ('active', '=', True)]):
    raise SystemExit('REFUSE: the A-Cube adapter is enabled for %s' % tenant_db)
if 'ncollection_uae_einvoicing' not in tenant._nc_effective_module_list():
    raise SystemExit('FAIL the engine is not in the effective module list (gate refused?)')

# install + licence, the synchronous forms of the queued route
tenant.action_install_licensed_modules_sync()
env.cr.commit()
tenant.invalidate_recordset()
if tenant.module_install_state not in ('done', 'none'):
    raise SystemExit('FAIL module install: %s' % tenant.module_install_state)
tenant.sync_workspace_config()
env.cr.commit()
print('INFO install', tenant.module_install_state, '| config sync', tenant.config_sync_state)
print('STEP_OK entitlement')
PY

echo "== tenant: ownership marker, e-invoicing manager, biz =="
[ "$(psql_db "$TENANT_DB" "SELECT state FROM ir_module_module WHERE name='ncollection_uae_einvoicing'")" = installed ] \
  || refuse "ncollection_uae_einvoicing is not installed on '$TENANT_DB' after the platform step"
if ! out=$("${DC[@]}" exec -T -e NC_E2E_MARK="$FIXTURE_MARK" odoo odoo shell -d "$TENANT_DB" --no-http \
             --log-level=warn "${DBARGS[@]}" 2>&1 <<'PY'
import os
ICP = env['ir.config_parameter'].sudo()
mark = os.environ['NC_E2E_MARK']
if ICP.get_param('ncollection.e2e.fixture') != mark:
    raise SystemExit('REFUSE: the ownership marker is missing or different (mark_tenant_db writes it)')
Users = env['res.users'].with_context(active_test=False)
owner = Users.search([('login', '=', 'e2e.owner')], limit=1)
if not owner:
    raise SystemExit('FAIL the provisioned owner e2e.owner does not exist')
manager = env.ref('ncollection_uae_einvoicing.group_einvoice_manager')
if manager not in owner.group_ids:
    owner.write({'group_ids': [(4, manager.id)]})
groups = [env.ref('base.group_user').id]
biz = Users.search([('login', '=', 'biz')], limit=1)
if biz:
    biz.write({'password': 'demo1234', 'group_ids': [(6, 0, groups)], 'active': True})
else:
    Users.create({'name': 'biz', 'login': 'biz', 'password': 'demo1234', 'group_ids': [(6, 0, groups)]})
env.cr.commit()
biz = Users.search([('login', '=', 'biz')], limit=1)
for xmlid in ('group_einvoice_user', 'group_einvoice_manager', 'group_einvoice_service'):
    if biz.has_group('ncollection_uae_einvoicing.' + xmlid):
        raise SystemExit('FAIL biz holds %s' % xmlid)
company = env.company
print('INFO company', company.country_id.code, company.currency_id.name, company.chart_template)
print('STEP_OK tenant')
PY
); then
  echo "❌ tenant step failed:" >&2; echo "$out" | tail -40 >&2; exit 1
fi
grep -q '^STEP_OK' <<<"$out" || { echo "❌ tenant step did not complete:" >&2; echo "$out" | tail -40 >&2; exit 1; }
grep -E '^(STEP_OK|INFO)' <<<"$out" | sed 's/^/  [tenant] /'

echo "✅ e2euae ready: http://${TENANT_DB}.localhost (owner e2e.owner, restricted user biz)"
