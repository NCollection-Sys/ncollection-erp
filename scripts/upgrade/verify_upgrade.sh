#!/usr/bin/env bash
# shellcheck disable=SC2015
# ============================================================================
#  #362 — module upgrade (-u) proof: migrations run, and data SURVIVES them
# ============================================================================
#  THE GAP THIS CLOSES
#  -------------------
#  Nothing exercised the `-u` path. `scripts/deploy/deploy.sh` runs no `-u` at
#  all (it deploys the image, then smoke-test.sh curls /web/health — liveness,
#  not correctness), and CI always installs FRESH with `-i`. Yet five modules
#  ship eight migration scripts that run exactly once, against live customer
#  data, on a database-per-tenant platform. That is the highest-risk code in the
#  repo and it had no test.
#
#  HOW AN OLD VERSION IS SIMULATED
#  -------------------------------
#  Not by checking out an old commit — that needs git gymnastics inside a test.
#  Odoo decides which migrations to run by comparing the manifest version against
#  `ir_module_module.latest_version`. So we install at HEAD, wind `latest_version`
#  BACK, and run `-u`: Odoo then replays every migration between the two. Winding
#  back to 19.0.1.1.0 replays subscription's 1.2.0, 1.3.0 and 1.4.0 — the whole
#  upgrade path, not just one step.
#
#  Fixtures are inserted with raw SQL on purpose. These migrations exist to clean
#  up LEGACY rows that today's ORM constraints would refuse to create; going
#  through the ORM would make the fixture impossible to build and the test
#  vacuous.
#
#  WHAT IS ASSERTED
#  ----------------
#    GREEN arm (upgrade must succeed and be surgical):
#      1. a PROVISIONED tenant with a legacy invalid database_name KEEPS it
#         — nulling it would orphan a live tenant database,
#      2. an UNPROVISIONED tenant with an invalid name has it CLEARED,
#      3. a tenant with a VALID name is untouched,
#      4. unrelated seeded data survives the upgrade.
#    RED arm (the guard must fire, loudly):
#      5. two tenants sharing a database_name abort the upgrade with the
#         actionable message naming the ids — NOT a raw UniqueViolation.
#
#  Fixture DBs: upgrgreen · upgrred · upgrcore  (prefix `upgr*`, this script only)
#  Idempotent: all three are dropped on entry and on exit (Rule 12).
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")/../.."   # repo root

GREEN_DB="${UPGRADE_GREEN_DB:-upgrgreen}"
RED_DB="${UPGRADE_RED_DB:-upgrred}"
DC=(docker compose)
DBARGS=(--db_host=db --db_user=odoo --db_password=odoo)

# The tenant module set plus ncollection_subscription, which owns the migrations
# under test. --no-http keeps this off :8069, which the dev container already
# serves (the clash that made the suite unrunnable locally — #365).
MODULES="base,ncollection_core,ncollection_branding,ncollection_auth,ncollection_subscription"

# Below subscription's oldest migration (19.0.1.2.0), so winding back here
# replays the FULL path 1.2.0 -> 1.3.0 -> 1.4.0.
OLD_VERSION="19.0.1.1.0"

# --- CORE arm (#381) -------------------------------------------------------
# ncollection_core's 19.0.1.15.2 post-migrate does SECURITY REPAIR in raw SQL
# and had ZERO automated coverage: this harness only ever wound back
# ncollection_subscription, so that script was never executed by any suite. It
# already shipped one bug found solely by a human reading it — its
# hand-maintained module list missed `product` and `purchase` from the
# transitive closure.
#
# It needs its OWN fixture because the sweep it performs deletes memberships of
# groups belonging to sale/crm/stock/hr/account/product/purchase. On the GREEN
# fixture those modules are not installed, so no such group exists, the DELETE
# matches nothing, and an assertion on it would pass while proving nothing —
# the vacuous-test shape this repo keeps getting bitten by.
CORE_DB="${UPGRADE_CORE_DB:-upgrcore}"
CORE_MODULES="$MODULES,sale,crm,stock,hr,purchase"
# Below 19.0.1.15.2 so the migration is replayed. 15.0 is the version that
# actually shipped the working `False` password this repairs.
CORE_OLD_VERSION="19.0.1.15.0"

pass=0; fail=0
ok(){ echo "  ✅ PASS: $1"; pass=$((pass + 1)); }
no(){ echo "  ❌ FAIL: $1"; fail=$((fail + 1)); }
hr(){ echo "----------------------------------------------------------------------"; }

drop_db(){
  "${DC[@]}" exec -T db psql -U odoo -d postgres -c \
    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$1' AND pid<>pg_backend_pid()" \
    >/dev/null 2>&1 || true
  "${DC[@]}" exec -T db dropdb -U odoo --if-exists "$1" >/dev/null 2>&1 || true
}
# Drop BOTH fixtures on any exit path, so a crash never leaves half-built state
# behind for the next run to trip over (Rule 12).
trap 'drop_db "$GREEN_DB"; drop_db "$RED_DB"; drop_db "$CORE_DB"' EXIT

q(){ "${DC[@]}" exec -T db psql -U odoo -d "$1" -tAc "$2" 2>/dev/null | tr -d '[:space:]'; }

build_fixture(){   # $1 = db ; $2 = modules (default: $MODULES)
  local db="$1" MODULES="${2:-$MODULES}"
  drop_db "$db"
  "${DC[@]}" exec -T db createdb -U odoo -O odoo "$db" >/dev/null 2>&1
  local _log; _log="$(mktemp)"
  if ! "${DC[@]}" exec -T odoo odoo -d "$db" -i "$MODULES" --without-demo=True --no-http \
       --stop-after-init "${DBARGS[@]}" >"$_log" 2>&1; then
    echo "REFUSING: could not install $MODULES on '$db' — the upgrade proof" >&2
    echo "  would compare two databases that were never built (#385)." >&2
    tail -25 "$_log" >&2; rm -f "$_log"; exit 1
  fi
  # Success branch only — a bare `rm` after the call cannot run under `set -e`,
  # so a refusal both leaked the temp file and discarded the evidence.
  if "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/scripts/dev/assert_odoo_setup.sh" \
       "$_log" "$MODULES on $db" "if ./oca is empty, run 'make oca'"; then
    rm -f "$_log"
  else
    echo "  Full setup log kept at: $_log" >&2
    exit 1
  fi
}

wind_back(){       # $1 = db ; $2 = module ; $3 = version — make Odoo believe
                   # the module is older than it is
  "${DC[@]}" exec -T db psql -U odoo -d "$1" -c \
    "UPDATE ir_module_module SET latest_version='$3' WHERE name='$2'" \
    >/dev/null 2>&1
}

upgrade(){         # $1 = db ; $2 = module ; exit code is odoo's, log on stdout
  "${DC[@]}" exec -T odoo odoo -d "$1" -u "$2" --no-http \
      --stop-after-init "${DBARGS[@]}" 2>&1
}

echo "#362 — module upgrade proof (DBs: $GREEN_DB, $RED_DB)"
hr

# =========================================================================
echo "== 1) GREEN arm: build a tenant DB with LEGACY rows the ORM would refuse =="
# =========================================================================
build_fixture "$GREEN_DB"

# Raw SQL: today's constraints would reject these names, which is the point.
"${DC[@]}" exec -T db psql -U odoo -d "$GREEN_DB" -c "
INSERT INTO ncollection_tenant
  (company_name, database_status, onboarding_stage, status, database_name)
VALUES
  ('Live Legacy Co',  'ready',           'done', 'active', 'live_legacy_db'),
  ('Pending Cruft Co','not_provisioned', 'done', 'trial',  'bad__name__cruft'),
  ('Valid Co',        'ready',           'done', 'active', 'validco');
" >/dev/null 2>&1

before_live="$(q "$GREEN_DB" "SELECT database_name FROM ncollection_tenant WHERE company_name='Live Legacy Co'")"
before_count="$(q "$GREEN_DB" "SELECT count(*) FROM ncollection_tenant")"
[ "$before_live" = "live_legacy_db" ] && ok "fixture seeded (provisioned tenant holds a legacy invalid name)" \
  || no "fixture seeding failed (got '$before_live')"

echo "== 2) wind the module version back to $OLD_VERSION and upgrade =="
wind_back "$GREEN_DB" ncollection_subscription "$OLD_VERSION"
seen="$(q "$GREEN_DB" "SELECT latest_version FROM ir_module_module WHERE name='ncollection_subscription'")"
[ "$seen" = "$OLD_VERSION" ] && ok "latest_version wound back to $OLD_VERSION" \
  || no "could not wind version back (got '$seen')"

if upgrade "$GREEN_DB" ncollection_subscription > /tmp/upgr_green.log 2>&1; then
  ok "upgrade completed (exit 0)"
else
  no "upgrade FAILED — see /tmp/upgr_green.log"
  tail -15 /tmp/upgr_green.log
fi
grep -qi "traceback (most recent call last)" /tmp/upgr_green.log \
  && no "traceback during upgrade" || ok "no traceback during upgrade"

echo "== 3) assert the migration was SURGICAL =="
after_live="$(q "$GREEN_DB" "SELECT coalesce(database_name,'<NULL>') FROM ncollection_tenant WHERE company_name='Live Legacy Co'")"
after_cruft="$(q "$GREEN_DB" "SELECT coalesce(database_name,'<NULL>') FROM ncollection_tenant WHERE company_name='Pending Cruft Co'")"
after_valid="$(q "$GREEN_DB" "SELECT coalesce(database_name,'<NULL>') FROM ncollection_tenant WHERE company_name='Valid Co'")"
after_count="$(q "$GREEN_DB" "SELECT count(*) FROM ncollection_tenant")"

[ "$after_live" = "live_legacy_db" ] \
  && ok "PROVISIONED tenant KEPT its legacy name (a live database was not orphaned)" \
  || no "provisioned tenant's database_name was changed to '$after_live' — this orphans a live tenant DB"
[ "$after_cruft" = "<NULL>" ] \
  && ok "UNPROVISIONED tenant's invalid name was cleared" \
  || no "unprovisioned invalid name survived as '$after_cruft'"
[ "$after_valid" = "validco" ] \
  && ok "VALID name untouched" || no "valid name was altered to '$after_valid'"
[ "$after_count" = "$before_count" ] \
  && ok "no rows lost across the upgrade ($after_count)" \
  || no "row count changed: $before_count -> $after_count"

# =========================================================================
echo "== 4) RED arm: duplicate database_names must abort LOUDLY =="
# =========================================================================
build_fixture "$RED_DB"

# The constraint must be dropped BEFORE seeding duplicates, and that is not a
# cheat — it is what a genuinely old database looks like. UNIQUE(database_name)
# is added BY the 19.0.1.2.0 upgrade; a database still on 19.0.1.1.0 does not
# have it, which is precisely why duplicates could accumulate there and why the
# pre-migrate guard exists at all.
#
# A first version of this arm seeded the duplicates against a HEAD install, where
# the constraint already exists — the INSERT was rejected instantly and, under
# `set -e`, killed the run. That failure was the test being wrong, not the
# product.
"${DC[@]}" exec -T db psql -U odoo -d "$RED_DB" -c \
  "ALTER TABLE ncollection_tenant DROP CONSTRAINT IF EXISTS ncollection_tenant_database_name_unique" \
  >/dev/null 2>&1
dropped="$(q "$RED_DB" "SELECT count(*) FROM pg_constraint WHERE conrelid='ncollection_tenant'::regclass AND conname='ncollection_tenant_database_name_unique'")"
[ "$dropped" = "0" ] && ok "pre-1.2.0 state simulated (UNIQUE(database_name) absent)" \
  || no "could not drop the constraint — the RED arm cannot be set up"

"${DC[@]}" exec -T db psql -U odoo -d "$RED_DB" -c "
INSERT INTO ncollection_tenant
  (company_name, database_status, onboarding_stage, status, database_name)
VALUES
  ('Dupe One', 'ready', 'done', 'active', 'sharedname'),
  ('Dupe Two', 'ready', 'done', 'active', 'sharedname');
" >/dev/null 2>&1
seeded="$(q "$RED_DB" "SELECT count(*) FROM ncollection_tenant WHERE database_name='sharedname'")"
[ "$seeded" = "2" ] && ok "two tenants now share a database_name" \
  || no "duplicate seeding failed (found $seeded rows)"
wind_back "$RED_DB" ncollection_subscription "$OLD_VERSION"

if upgrade "$RED_DB" ncollection_subscription > /tmp/upgr_red.log 2>&1; then
  no "upgrade SUCCEEDED with duplicate database_names — the guard did not fire"
else
  ok "upgrade refused to proceed (guard fired)"
fi
if grep -q "share a database name; reconcile them before upgrading" /tmp/upgr_red.log; then
  ok "failure message is ACTIONABLE (names the tenants to reconcile)"
else
  no "failed, but not with the actionable message — ops would see a raw error"
  grep -iE "error|exception" /tmp/upgr_red.log | tail -3
fi
grep -q "UniqueViolation" /tmp/upgr_red.log \
  && no "aborted with a raw UniqueViolation — the pre-migrate guard is what should catch this" \
  || ok "did not degenerate into a raw UniqueViolation"

# =========================================================================
echo "== 5) CORE arm: ncollection_core's security-repair migration (#381) =="
# =========================================================================
# 19.0.1.15.2/post-migrate.py repairs a shipped credential and a broken grant.
# Until now nothing executed it. Its own module list already shipped one bug
# (missing `product` and `purchase` from the transitive closure) caught only by
# a human reading the diff.
build_fixture "$CORE_DB" "$CORE_MODULES"

cron_uid="$(q "$CORE_DB" "SELECT res_id FROM ir_model_data WHERE module='ncollection_core' AND name='user_cron_service' AND model='res.users'")"
cron_gid="$(q "$CORE_DB" "SELECT res_id FROM ir_model_data WHERE module='ncollection_core' AND name='group_cron_service' AND model='res.groups'")"
if [ -n "$cron_uid" ] && [ -n "$cron_gid" ]; then
  ok "cron service user and group resolved (uid=$cron_uid gid=$cron_gid)"
else
  no "could not resolve the cron user/group — the CORE arm cannot be set up"
  hr; echo "SUMMARY: $pass passed, $fail failed."; exit 1
fi

echo "== 5a) simulate a tenant that installed 19.0.1.15.0 =="
# Raw SQL on purpose: this is the damaged state a real tenant is IN, and the
# ORM would refuse to reproduce most of it.
"${DC[@]}" exec -T db psql -U odoo -d "$CORE_DB" -c "
-- (1) 15.0 wrote <field name=\"password\">False</field> with no eval=, so the
--     loader stored the literal string and hashed it: a WORKING login.
UPDATE res_users SET password='rv381-simulated-working-hash' WHERE id=$cron_uid;
-- (2) group_cron_service did not exist in 15.0, so the user is not in it.
DELETE FROM res_groups_users_rel WHERE uid=$cron_uid AND gid=$cron_gid;
-- (3) an interim build linked app-user groups to group_cron_service ...
INSERT INTO res_groups_implied_rel (gid, hid)
SELECT $cron_gid, d.res_id FROM ir_model_data d
 WHERE d.model='res.groups' AND d.module='sales_team' LIMIT 1
ON CONFLICT DO NOTHING;
-- ... and Odoo materialises the FULL transitive closure onto the user, which
--     is why dropping the implication alone does not revoke anything.
INSERT INTO res_groups_users_rel (uid, gid)
SELECT DISTINCT $cron_uid, d.res_id FROM ir_model_data d
 WHERE d.model='res.groups' AND d.module IN ('sales_team','sale','crm','stock','hr','account','product','purchase')
ON CONFLICT DO NOTHING;
" >/dev/null 2>&1

b_pw="$(q "$CORE_DB" "SELECT count(*) FROM res_users WHERE id=$cron_uid AND password IS NOT NULL")"
b_member="$(q "$CORE_DB" "SELECT count(*) FROM res_groups_users_rel WHERE uid=$cron_uid AND gid=$cron_gid")"
b_swept="$(q "$CORE_DB" "SELECT count(*) FROM res_groups_users_rel r JOIN ir_model_data d ON d.res_id=r.gid AND d.model='res.groups' WHERE r.uid=$cron_uid AND d.module IN ('sales_team','sale','crm','stock','hr','account','product','purchase')")"
b_pp="$(q "$CORE_DB" "SELECT count(*) FROM res_groups_users_rel r JOIN ir_model_data d ON d.res_id=r.gid AND d.model='res.groups' WHERE r.uid=$cron_uid AND d.module IN ('product','purchase')")"
b_impl="$(q "$CORE_DB" "SELECT count(*) FROM res_groups_implied_rel WHERE gid=$cron_gid")"

# The fixture must be REAL before the assertions on it mean anything. A sweep
# assertion over an empty set passes while proving nothing — the exact vacuous
# shape #381 was filed about.
[ "$b_pw" = "1" ]     && ok "fixture: the cron user has a working password" || no "fixture: password not set (got $b_pw)"
[ "$b_member" = "0" ] && ok "fixture: not a member of group_cron_service"   || no "fixture: already a member (got $b_member)"
[ "${b_swept:-0}" -gt 0 ] && ok "fixture: $b_swept app-group membership(s) materialised" || no "fixture: no app-group memberships to sweep — the arm would be vacuous"
[ "${b_pp:-0}" -gt 0 ]    && ok "fixture: $b_pp of them are product/purchase (the closure bug)" || no "fixture: no product/purchase memberships — the regression this pins is unexercised"
[ "${b_impl:-0}" -gt 0 ]  && ok "fixture: $b_impl implication(s) on group_cron_service" || no "fixture: no implication, so the migration's sweep branch will not run"

echo "== 5b) wind ncollection_core back to $CORE_OLD_VERSION and upgrade =="
wind_back "$CORE_DB" ncollection_core "$CORE_OLD_VERSION"
seen_core="$(q "$CORE_DB" "SELECT latest_version FROM ir_module_module WHERE name='ncollection_core'")"
[ "$seen_core" = "$CORE_OLD_VERSION" ] && ok "ncollection_core wound back to $CORE_OLD_VERSION" \
  || no "could not wind ncollection_core back (got '$seen_core')"

if upgrade "$CORE_DB" ncollection_core > /tmp/upgr_core.log 2>&1; then
  ok "ncollection_core upgrade completed (exit 0)"
else
  no "ncollection_core upgrade FAILED — see /tmp/upgr_core.log"
  tail -15 /tmp/upgr_core.log
fi
grep -qi "traceback (most recent call last)" /tmp/upgr_core.log \
  && no "traceback during the core upgrade" || ok "no traceback during the core upgrade"

echo "== 5c) assert the security repair actually happened =="
a_pw="$(q "$CORE_DB" "SELECT count(*) FROM res_users WHERE id=$cron_uid AND password IS NOT NULL")"
a_member="$(q "$CORE_DB" "SELECT count(*) FROM res_groups_users_rel WHERE uid=$cron_uid AND gid=$cron_gid")"
a_swept="$(q "$CORE_DB" "SELECT count(*) FROM res_groups_users_rel r JOIN ir_model_data d ON d.res_id=r.gid AND d.model='res.groups' WHERE r.uid=$cron_uid AND d.module IN ('sales_team','sale','crm','stock','hr','account','product','purchase')")"
a_pp="$(q "$CORE_DB" "SELECT count(*) FROM res_groups_users_rel r JOIN ir_model_data d ON d.res_id=r.gid AND d.model='res.groups' WHERE r.uid=$cron_uid AND d.module IN ('product','purchase')")"
a_impl="$(q "$CORE_DB" "SELECT count(*) FROM res_groups_implied_rel WHERE gid=$cron_gid")"

[ "$a_pw" = "0" ] && ok "the shipped credential is GONE (password IS NULL)" \
  || no "the cron user can still authenticate — the credential survived the upgrade"
[ "$a_member" = "1" ] && ok "restored to group_cron_service (alerts can be recorded)" \
  || no "not in group_cron_service — every detected anomaly would be discarded silently"
[ "$a_swept" = "0" ] && ok "all $b_swept materialised app-group membership(s) revoked" \
  || no "$a_swept app-group membership(s) survived — the scheduler keeps write/unlink it should not have"
[ "$a_pp" = "0" ] && ok "product/purchase memberships revoked (the closure bug stays fixed)" \
  || no "$a_pp product/purchase membership(s) survived — the exact bug review caught is back"
[ "$a_impl" = "0" ] && ok "app-user implications removed from group_cron_service" \
  || no "$a_impl implication(s) survived on group_cron_service"

hr
echo "SUMMARY: $pass passed, $fail failed."
[ "$fail" -eq 0 ] || exit 1
echo "✅ Upgrades run their migrations, and data survives them."
