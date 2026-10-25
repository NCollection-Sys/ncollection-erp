#!/usr/bin/env bash
# shellcheck disable=SC2015
# ============================================================================
#  P2-T01 — end-to-end provisioning proof (local; heavy — not a CI test)
# ============================================================================
#  Proves, against a platform DB that has ncollection_saas installed:
#    1. HAPPY PATH: a queued job produces a login-ready tenant DB (plan modules
#       installed, workspace.config written, admin renamed + reset-forced),
#    2. ROLLBACK: a job whose install fails drops the half-created DB — no zombie.
#    3. COMPANION MODULES: cheque printing follows Accounting. A plan with `account` provisions
#       ncollection_cheque_print; a plan without it provisions neither (see ncollection_subscription
#       models/companions.py). The REAL engine, a real database: the unit tests only prove the decision.
#
#  The CI-safe unit tests (tests/test_provisioning.py) cover the pure logic;
#  THIS exercises the real odoo subprocesses + psycopg2 rollback, which is why
#  it runs locally, not in CI. Usage:  PLATFORM_DB=saastest bash <this>
# ============================================================================
set -euo pipefail
# (SC2015 disabled file-wide above: `cond && ok || no` is intentional — ok/no
#  only echo + count and always return 0, so `|| no` runs iff cond is false.)
cd "$(dirname "$0")/../../../.."   # repo root

PLATFORM_DB="${PLATFORM_DB:-saastest}"
# Safety: this script DELETES fixture rows (plans PROV/PROVNOA/FAIL, provclient/provnoacct/provfail
# tenants) from the platform DB. Unlike tenant db_names, the plan codes have no
# reserved-code registry, so refuse to run unless PLATFORM_DB is obviously a test
# DB — a guard against PLATFORM_DB ever being pointed at real/shared platform data.
case "$PLATFORM_DB" in
  *test*) ;;
  *) echo "REFUSING: PLATFORM_DB='$PLATFORM_DB' is not a recognized test database " \
          "(expected a name containing 'test', e.g. saastest). This script deletes " \
          "platform fixture rows and must never run against real data." >&2; exit 1;;
esac
DC=(docker compose)
DBARGS=(--db_host=db --db_user=odoo --db_password=odoo)
pass=0; fail=0
ok(){ echo "  ✅ PASS: $1"; pass=$((pass + 1)); }
no(){ echo "  ❌ FAIL: $1"; fail=$((fail + 1)); }
hr(){ echo "----------------------------------------------------------------------"; }

db_exists(){ "${DC[@]}" exec -T db psql -U odoo -d postgres -tAc \
  "SELECT 1 FROM pg_database WHERE datname='$1'" 2>/dev/null | grep -q 1; }
drop_db(){ "${DC[@]}" exec -T db psql -U odoo -d postgres -c \
  "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$1'" >/dev/null 2>&1 || true
  "${DC[@]}" exec -T db psql -U odoo -d postgres -c "DROP DATABASE IF EXISTS $1" >/dev/null 2>&1 || true; }
tq(){ "${DC[@]}" exec -T db psql -U odoo -d "$1" -tAc "$2" 2>/dev/null | tr -d ' '; }

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
  # BEFORE the upgrade, not after (#388). The setup check that follows reads
  # odoo's log for markers that are DATABASE-WIDE — odoo builds its graph from
  # every installed module on the DB, not just the one named on the command
  # line — so a module left mid-transition by unrelated history makes that
  # check refuse and blame ncollection_saas. PLATFORM_DB is persistent
  # (CLAUDE.md: saastest/ncplatform, "do not drop"), which makes it the one
  # database where such a row can accumulate and then refuse every future
  # upgrade. This names the real culprit first.
  "$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)/scripts/dev/assert_modules_settled.sh" \
    "$PLATFORM_DB" "the ncollection_saas upgrade"
  # Output CAPTURED, not discarded: odoo exits 0 having SKIPPED a module whose
  # dependency is missing, so the exit status below cannot see that on its own
  # (#385 — it cost an invalid gate run elsewhere in this suite).
  _setup_log="$(mktemp)"
  if ! "${DC[@]}" exec -T odoo odoo -d "$PLATFORM_DB" -u ncollection_saas \
       --stop-after-init --no-http --log-level=warn "${DBARGS[@]}" >"$_setup_log" 2>&1; then
    echo "REFUSING: could not upgrade ncollection_saas on '$PLATFORM_DB'." >&2
    echo "  Running on a stale schema would report a code failure that is" >&2
    echo "  really a setup problem. Fix the upgrade, then re-run." >&2
    exit 1
  fi
  # The `rm` is in the SUCCESS branch only, on purpose. Placed after a bare call
  # it would never run under `set -e` — the abort path both leaked the file and
  # threw away the one artefact worth reading. On a refusal the asserter prints
  # 6 matching lines; the full log is what you actually want next, so keep it
  # and say where.
  if "$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)/scripts/dev/assert_odoo_setup.sh" \
       "$_setup_log" "ncollection_saas on $PLATFORM_DB" \
       "if ./oca is empty, run 'make oca' — queue_job lives there"; then
    rm -f "$_setup_log"
  else
    echo "  Full setup log kept at: $_setup_log" >&2
    exit 1
  fi
}
platform_schema_sync

# Remove THIS script's fixture rows from the PLATFORM DB (plans PROV/PROVNOA/FAIL, the
# provclient/provnoacct/provfail tenants + their jobs). Idempotency (Rule 12): without this
# a re-run collides on the unique plan code 'PROV'. ORM unlink (not raw psql) so
# FK/ondelete cascades are respected; jobs -> tenants -> plans is FK-safe; an
# empty search is a no-op, so this is itself idempotent.
#   - active_test=False: an ARCHIVED leftover row is hidden from a plain search
#     but STILL collides on the unique code — search all so cleanup can't miss it.
#   - output is captured and printed ONLY on failure: quiet on success, but a
#     real cleanup failure fails loud with its traceback (Rule 10), since (unlike
#     the happy/rollback blocks) nothing else probes this step's result.
platform_cleanup(){
  local out
  if ! out=$("${DC[@]}" exec -T odoo odoo shell -d "$PLATFORM_DB" --no-http \
             --log-level=warn "${DBARGS[@]}" 2>&1 <<'PY'
env['ncollection.provisioning.job'].with_context(active_test=False).search(
    [('database_name', 'in', ['provclient', 'provnoacct', 'provfail'])]).unlink()
env['ncollection.tenant'].with_context(active_test=False).search(
    [('database_name', 'in', ['provclient', 'provnoacct', 'provfail'])]).unlink()
env['ncollection.subscription.plan'].with_context(active_test=False).search(
    [('code', 'in', ['PROV', 'PROVNOA', 'FAIL'])]).unlink()
env.cr.commit()
PY
  ); then
    echo "❌ platform_cleanup FAILED (platform DB '$PLATFORM_DB'):" >&2
    echo "$out" | tail -30 >&2
    return 1
  fi
}

echo "== cleanup any prior run =="
drop_db provclient; drop_db provnoacct; drop_db provfail
platform_cleanup

echo "== HAPPY PATH: provision 'provclient' (plan modules: crm, account) =="
"${DC[@]}" exec -T odoo odoo shell -d "$PLATFORM_DB" --no-http --log-level=warn "${DBARGS[@]}" <<'PY' 2>/dev/null
plan = env['ncollection.subscription.plan'].create({
    'name': 'Prov Plan', 'code': 'PROV', 'allowed_module_names': 'crm, account', 'max_users': 3})
tenant = env['ncollection.tenant'].create({
    'company_name': 'Prov Co', 'database_name': 'provclient',
    'email': 'owner@prov.example', 'admin_login': 'provadmin',
    'admin_password': 'ProvKnown-Pass-77!', 'admin_password_confirm': 'ProvKnown-Pass-77!',
    'plan_id': plan.id, 'status': 'trial'})
job = env['ncollection.provisioning.job'].create({
    'tenant_id': tenant.id, 'database_name': 'provclient'})
env.cr.commit()
job.action_run_sync()
print('RESULT job=%s tenant_db_status=%s' % (job.status, tenant.database_status))
env.cr.commit()
PY
hr
echo "== verify the tenant DB 'provclient' =="
db_exists provclient && ok "database 'provclient' created" || no "database not created"
[ "$(tq provclient "SELECT state FROM ir_module_module WHERE name='crm'")" = "installed" ] \
  && ok "plan module 'crm' installed in tenant DB" || no "crm not installed"
[ "$(tq provclient "SELECT state FROM ir_module_module WHERE name='ncollection_core'")" = "installed" ] \
  && ok "ncollection_core installed" || no "core not installed"
# #178: every tenant gets auth hardening by default (idle-session timeout + auth log).
[ "$(tq provclient "SELECT state FROM ir_module_module WHERE name='ncollection_auth'")" = "installed" ] \
  && ok "ncollection_auth installed (idle-timeout + login audit, #178)" || no "ncollection_auth not installed"
[ "$(tq provclient "SELECT state FROM ir_module_module WHERE name='auth_session_timeout'")" = "installed" ] \
  && ok "auth_session_timeout (OCA dep) auto-installed" || no "auth_session_timeout not installed"
[ "$(tq provclient "SELECT plan_code FROM ncollection_workspace_config LIMIT 1")" = "PROV" ] \
  && ok "workspace.config projection written (plan_code=PROV)" || no "workspace config missing"
# The login is the USERNAME the tenant was given, and the contact email must NOT
# have become a login: that was the old behaviour and is now forbidden.
[ "$(tq provclient "SELECT login FROM res_users WHERE login='provadmin'")" = "provadmin" ] \
  && ok "tenant admin seeded (login is the username, not the email)" || no "admin not seeded"
[ "$(tq provclient "SELECT COUNT(*) FROM res_users WHERE login='owner@prov.example'")" = "0" ] \
  && ok "the contact email was NOT turned into a login" || no "the email became a login"
# The credentials typed at creation must be the ones that work at the tenant, and a wrong one must not.
cred=$("${DC[@]}" exec -T odoo odoo shell -d provclient --no-http --log-level=error "${DBARGS[@]}" <<'PY' 2>/dev/null
user = env['res.users'].search([('login', '=', 'provadmin')])
def good(pw):
    try:
        user.with_user(user)._check_credentials({'type': 'password', 'password': pw}, {'interactive': True})
        return True
    except Exception:
        return False
print('CRED', good('ProvKnown-Pass-77!'), good('ProvKnown-Pass-78!'), user.active)
PY
)
echo "$cred" | grep -q "CRED True False True" \
  && ok "the password entered at tenant creation authenticates at the tenant; a wrong one is refused" \
  || no "creation credentials do not work at the provisioned tenant: $cred"
# R-014 guard: the seed re-runs _sync_role_implications AFTER the plan modules
# install, so the Accountant role must now imply account.group_account_user
# (core's post_init_hook alone links nothing — account did not exist yet).
[ "$(tq provclient "SELECT COUNT(*) FROM res_groups_implied_rel r \
  JOIN ir_model_data role ON role.model='res.groups' AND role.module='ncollection_core' \
    AND role.name='group_role_accountant' AND role.res_id=r.gid \
  JOIN ir_model_data acc ON acc.model='res.groups' AND acc.module='account' \
    AND acc.name='group_account_user' AND acc.res_id=r.hid")" = "1" ] \
  && ok "R-014: Accountant role linked to account.group_account_user post-install" \
  || no "R-014: accountant role NOT linked to the account group"

# R-037 / Company Entry: a FRESHLY provisioned tenant must START consistent with the code that provisioned it: no
# module recorded behind its code, no model without its table, no stored field without its column. Provisioning
# installs in a subprocess and the platform's first call into the tenant then goes through the long-running server;
# a tenant that began behind would answer HTTP 500 on that very first request (how Company Entry failed on Atlas-eg).
drift="$("${DC[@]}" exec -T odoo odoo shell -d provclient --no-http --log-level=error "${DBARGS[@]}" \
  < scripts/upgrade/schema_drift.py 2>/dev/null | grep -a '^SCHEMA_DRIFT=' | head -1 | tr -d '\r' || true)"
[ "$drift" = "SCHEMA_DRIFT=0" ] \
  && ok "R-037: the freshly provisioned tenant starts with no module behind its code and no missing table or column" \
  || no "R-037: the freshly provisioned tenant is already behind its code or missing schema ($drift)"
# Its address is the one the configured base domain builds: what the welcome mail links to and Company Entry uses.
ORIGIN_PY="$(cat <<'PY'
tenant = env['ncollection.tenant'].search([('database_name', '=', 'provclient')], limit=1)
print('ORIGIN=%s' % (tenant._nc_public_origin() or ''))
PY
)"
want="$(printf '%s\n' "$ORIGIN_PY" | "${DC[@]}" exec -T odoo odoo shell -d "$PLATFORM_DB" --no-http --log-level=error \
  "${DBARGS[@]}" 2>/dev/null | grep -a '^ORIGIN=' | head -1 | tr -d '\r' || true)"
base="$(tq "$PLATFORM_DB" "SELECT portal_url FROM ncollection_tenant WHERE database_name='provclient'")"
[ -n "$base" ] && [ "ORIGIN=$base" = "$want" ] \
  && ok "the tenant's recorded portal URL is the canonical builder's address for the configured base domain" \
  || no "portal URL '$base' differs from the canonical builder ('$want')"
hr

echo "== COMPANION MODULES: cheque printing follows Accounting (real provisioning, real databases) =="
# 'provclient' was provisioned from a plan that names `account` and does NOT name ncollection_cheque_print: the module can
# only be there because the entitlement authority derived it from its host.
[ "$(tq provclient "SELECT state FROM ir_module_module WHERE name='account'")" = "installed" ] \
  && ok "plan with Accounting: account installed" || no "account not installed"
[ "$(tq provclient "SELECT state FROM ir_module_module WHERE name='ncollection_cheque_print'")" = "installed" ] \
  && ok "plan with Accounting: ncollection_cheque_print provisioned automatically" \
  || no "ncollection_cheque_print NOT installed although the plan has Accounting"
[ "$(tq provclient "SELECT COUNT(*) FROM ncollection_cheque_template")" = "3" ] \
  && ok "the three provisional cheque templates were seeded" || no "cheque templates not seeded"
[ "$(tq provclient "SELECT COUNT(*) FROM ncollection_cheque_template WHERE dimensions_confirmed")" = "0" ] \
  && ok "every shipped template is still UNCONFIRMED (dimensions_confirmed = false)" || no "a template claims confirmed dimensions"
tq provclient "SELECT allowed_module_names FROM ncollection_workspace_config LIMIT 1" | tr "," "\n" | grep -qx ncollection_cheque_print \
  && ok "the tenant's licence names it (Ring 1/2 follow Accounting)" || no "the tenant's allowed list does not name it"

echo "-- the other side of the boundary: a plan WITHOUT Accounting provisions neither --"
"${DC[@]}" exec -T odoo odoo shell -d "$PLATFORM_DB" --no-http --log-level=warn "${DBARGS[@]}" <<'PY' 2>/dev/null
plan = env['ncollection.subscription.plan'].create({
    'name': 'Prov NoAcct', 'code': 'PROVNOA', 'allowed_module_names': 'crm', 'max_users': 3})
tenant = env['ncollection.tenant'].create({
    'company_name': 'Prov NoAcct Co', 'database_name': 'provnoacct',
    'email': 'owner@provnoacct.example', 'admin_login': 'provnoadmin',
    'plan_id': plan.id, 'status': 'trial'})
job = env['ncollection.provisioning.job'].create({
    'tenant_id': tenant.id, 'database_name': 'provnoacct'})
env.cr.commit()
job.action_run_sync()
print('RESULT job=%s tenant_db_status=%s' % (job.status, tenant.database_status))
env.cr.commit()
PY
db_exists provnoacct && ok "database 'provnoacct' created" || no "provnoacct not created"
[ "$(tq provnoacct "SELECT state FROM ir_module_module WHERE name='crm'")" = "installed" ] \
  && ok "plan without Accounting: its own module (crm) installed" || no "crm not installed"
[ "$(tq provnoacct "SELECT state FROM ir_module_module WHERE name='account'")" != "installed" ] \
  && ok "plan without Accounting: account NOT installed (no plan was widened)" || no "account installed without being licensed"
[ "$(tq provnoacct "SELECT COALESCE((SELECT state FROM ir_module_module WHERE name='ncollection_cheque_print'),'absent')")" != "installed" ] \
  && ok "plan without Accounting: ncollection_cheque_print NOT installed" \
  || no "ncollection_cheque_print installed in a tenant without Accounting"
hr

echo "== ROLLBACK: DB created then a step fails -> must drop the half-built DB =="
# An unknown module name is silently ignored by `odoo -i`, so force a real
# post-create failure by making the seed step raise: the DB exists, then the
# engine must roll it back (SECURITY §11, no zombie).
"${DC[@]}" exec -T odoo odoo shell -d "$PLATFORM_DB" --no-http --log-level=warn "${DBARGS[@]}" <<'PY' 2>/dev/null
from unittest.mock import patch
plan = env['ncollection.subscription.plan'].create({
    'name': 'Fail Plan', 'code': 'FAIL', 'allowed_module_names': ''})
tenant = env['ncollection.tenant'].create({
    'company_name': 'Fail Co', 'database_name': 'provfail', 'admin_login': 'failadmin',
    'plan_id': plan.id, 'status': 'trial'})
job = env['ncollection.provisioning.job'].create({'tenant_id': tenant.id, 'database_name': 'provfail'})
env.cr.commit()
try:
    with patch.object(type(job), '_seed_tenant', side_effect=Exception('forced seed failure')):
        job.action_run_sync()
except Exception as e:
    print('EXPECTED_FAILURE %s' % type(e).__name__)
print('RESULT job=%s tenant_db_status=%s' % (job.status, tenant.database_status))
env.cr.commit()
PY
hr
echo "== verify rollback left no zombie DB =="
db_exists provfail && no "'provfail' still exists — ZOMBIE (rollback failed)" || ok "'provfail' dropped — no zombie DB"
[ "$(tq "$PLATFORM_DB" "SELECT status FROM ncollection_provisioning_job WHERE database_name='provfail' ORDER BY id DESC LIMIT 1")" = "failed" ] \
  && ok "job marked 'failed' with logs" || no "job not marked failed"
hr

echo "== cleanup =="
drop_db provclient; drop_db provnoacct; drop_db provfail
platform_cleanup
echo "SUMMARY: ${pass} passed, ${fail} failed."
[ "$fail" -eq 0 ] && echo "✅ Provisioning engine verified (create + rollback)." || echo "❌ FAILURES above."
exit "$([ "$fail" -eq 0 ] && echo 0 || echo 1)"
