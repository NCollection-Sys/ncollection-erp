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
# --- DRIFT arm (R-037) ------------------------------------------------------
# Code ahead of the database -> HTTP 500. Fixture prefix `upgr*`, like the other arms.
DRIFT_DB="${UPGRADE_DRIFT_DB:-upgrdrift}"
# hr_attendance is listed separately on purpose: the detector model
# `hr.attendance` lives in hr_attendance, not hr. Installing only `hr` leaves
# that model absent, _sync_scheduler_read_access skips it by design, and the
# grant assertion below would quietly cover 4 of 5 models.
CORE_MODULES="$MODULES,sale,crm,stock,hr,hr_attendance,purchase"
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
# COLLIDE_DB is the scratch name a copy-mode rehearsal of DRIFT_DB would use; 6c creates it on purpose (to prove a
# rehearsal refuses rather than drops a database it did not create) and 6d lets the rehearsal create and drop it.
COLLIDE_DB="upgrreh$DRIFT_DB"
trap 'drop_db "$GREEN_DB"; drop_db "$RED_DB"; drop_db "$CORE_DB"; drop_db "$DRIFT_DB"; drop_db "$COLLIDE_DB"; drop_db upgrcronnew' EXIT

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
echo "== 0) self-tests of the release preflight and of the module rule, BEFORE either is trusted below =="
# =========================================================================
# Pure stdlib, no database: run in the odoo container (its python3 is the one the preflight runs on), from a copy.
# shellcheck disable=SC2016  # the sh -c script expands in the CONTAINER's shell
if tar -C scripts/upgrade -cf - preflight_versions.py test_preflight_versions.py \
     | "${DC[@]}" exec -T odoo sh -c 'd="$(mktemp -d)"; tar -C "$d" -xf - && python3 "$d/test_preflight_versions.py"; rc=$?; rm -rf "$d"; exit $rc' \
     >/tmp/upgr_selftest.log 2>&1; then
  ok "preflight / module-rule self-tests pass ($(grep -ac ' ok$' /tmp/upgr_selftest.log) tests: base, all, out-of-root and unplain names refused; nothing-checked and missing-code are exit 2)"
else
  no "preflight / module-rule self-tests FAILED - nothing below that relies on them can be trusted"; tail -30 /tmp/upgr_selftest.log
fi

# =========================================================================
echo "== 1) GREEN arm: build a tenant DB with LEGACY rows the ORM would refuse =="
# =========================================================================
build_fixture "$GREEN_DB"

# Raw SQL: today's constraints would reject these names, which is the point.
"${DC[@]}" exec -T db psql -U odoo -d "$GREEN_DB" -c "
INSERT INTO ncollection_tenant
  (company_name, database_status, onboarding_stage, status, database_name, registration_state)
VALUES
  ('Live Legacy Co',  'ready',           'done', 'active', 'live_legacy_db', 'approved'),
  ('Pending Cruft Co','not_provisioned', 'done', 'trial',  'bad__name__cruft', 'approved'),
  ('Valid Co',        'ready',           'done', 'active', 'validco', 'approved');
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
  (company_name, database_status, onboarding_stage, status, database_name, registration_state)
VALUES
  ('Dupe One', 'ready', 'done', 'active', 'sharedname', 'approved'),
  ('Dupe Two', 'ready', 'done', 'active', 'sharedname', 'approved');
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
-- (4) the READ side. ROLE_IMPLICATIONS is applied by post_init_hook, which runs
--     on INSTALL ONLY, so a tenant UPGRADING from 15.0 never got these ACLs.
--     This fixture is built with -i at HEAD, where post_init_hook ALREADY
--     granted them — so without this DELETE the damaged state is never
--     reproduced and an assertion on the re-grant passes no matter what the
--     migration does. Review caught exactly that: the first version of this arm
--     left every one of its 14 checks green with the migration's
--     _sync_scheduler_read_access call deleted outright.
DELETE FROM ir_model_access
 WHERE group_id = $cron_gid
   AND model_id IN (SELECT id FROM ir_model WHERE model IN ('sale.order','account.move','hr.attendance','stock.quant','stock.warehouse.orderpoint'));
" >/dev/null 2>&1

b_pw="$(q "$CORE_DB" "SELECT count(*) FROM res_users WHERE id=$cron_uid AND password IS NOT NULL")"
b_member="$(q "$CORE_DB" "SELECT count(*) FROM res_groups_users_rel WHERE uid=$cron_uid AND gid=$cron_gid")"
b_swept="$(q "$CORE_DB" "SELECT count(*) FROM res_groups_users_rel r JOIN ir_model_data d ON d.res_id=r.gid AND d.model='res.groups' WHERE r.uid=$cron_uid AND d.module IN ('sales_team','sale','crm','stock','hr','account','product','purchase')")"
b_pp="$(q "$CORE_DB" "SELECT count(*) FROM res_groups_users_rel r JOIN ir_model_data d ON d.res_id=r.gid AND d.model='res.groups' WHERE r.uid=$cron_uid AND d.module IN ('product','purchase')")"
b_impl="$(q "$CORE_DB" "SELECT count(*) FROM res_groups_implied_rel WHERE gid=$cron_gid")"
b_read="$(q "$CORE_DB" "SELECT count(*) FROM ir_model_access WHERE group_id=$cron_gid AND model_id IN (SELECT id FROM ir_model WHERE model IN ('sale.order','account.move','hr.attendance','stock.quant','stock.warehouse.orderpoint'))")"
# Expected count is DERIVED from which detector models this database actually
# has, never hardcoded: _sync_scheduler_read_access skips absent models by
# design, so a hardcoded number would fail for the wrong reason the day the
# model list changes.
n_models="$(q "$CORE_DB" "SELECT count(*) FROM ir_model WHERE model IN ('sale.order','account.move','hr.attendance','stock.quant','stock.warehouse.orderpoint')")"

# The fixture must be REAL before the assertions on it mean anything. A sweep
# assertion over an empty set passes while proving nothing — the exact vacuous
# shape #381 was filed about.
[ "$b_pw" = "1" ]     && ok "fixture: the cron user has a working password" || no "fixture: password not set (got $b_pw)"
[ "$b_member" = "0" ] && ok "fixture: not a member of group_cron_service"   || no "fixture: already a member (got $b_member)"
[ "${b_swept:-0}" -gt 0 ] && ok "fixture: $b_swept app-group membership(s) materialised" || no "fixture: no app-group memberships to sweep — the arm would be vacuous"
[ "${b_pp:-0}" -gt 0 ]    && ok "fixture: $b_pp of them are product/purchase (the closure bug)" || no "fixture: no product/purchase memberships — the regression this pins is unexercised"
[ "${b_impl:-0}" -gt 0 ]  && ok "fixture: $b_impl implication(s) on group_cron_service" || no "fixture: no implication, so the migration's sweep branch will not run"
[ "${n_models:-0}" -gt 0 ] && ok "fixture: $n_models detector model(s) present to grant on" || no "fixture: no detector models — the read-side assertion would be vacuous"
[ "$b_read" = "0" ] && ok "fixture: scheduler read ACLs stripped (the upgrading-tenant state)" \
  || no "fixture: $b_read read ACL(s) still present — the re-grant would pass without the migration"

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
a_read="$(q "$CORE_DB" "SELECT count(*) FROM ir_model_access WHERE group_id=$cron_gid AND model_id IN (SELECT id FROM ir_model WHERE model IN ('sale.order','account.move','hr.attendance','stock.quant','stock.warehouse.orderpoint'))")"
# STATED PRECISELY, because the obvious reading of this line is wrong. It
# asserts the INVARIANT — after an upgrade the scheduler can read the detector
# models — NOT that post-migrate.py's _sync_scheduler_read_access call is what
# delivered it. Those are different claims and this arm cannot separate them.
#
# Measured: deleting that call from the migration outright leaves this suite at
# 30 passed, 0 failed. The reason is alert.py's _register_hook (models/anomaly/
# alert.py), which re-grants whenever `granted < present` on ANY registry load
# — the R-014 belt-and-braces fix. So the migration's call is redundant with a
# mechanism that runs on every boot, and no assertion reachable from here can
# tell the two apart.
#
# Review asked for this assertion to close the read-side gap. It does not close
# it, and saying so is the point: the invariant is worth pinning, the coverage
# claim is not available. Isolating the migration's call would mean disabling
# _register_hook in the fixture, which tests a configuration we never ship.
[ "$a_read" = "$n_models" ] && ok "scheduler CAN read all $n_models detector model(s) after the upgrade (invariant, not attribution)" \
  || no "read granted on $a_read of $n_models detector model(s) — the scheduler would write alerts it cannot base on anything"

# =========================================================================
echo "== 6) DRIFT arm (R-037): code ahead of the database -> detected, 500 reproduced, cured by a TARGETED upgrade =="
# =========================================================================
# Arms 1-5 prove that migrations REPLAY. None of them served a single HTTP request, so none could see the failure
# that keeps recurring: newer code under a database that has not been upgraded. Odoo never compares versions; the
# first request that reads a changed model dies with `column res_users.nc_must_change_password does not exist` (HTTP
# 500 on GET /web/login) while /web/health stays green. This arm rehearses that exact lifecycle on a scratch DB with
# scripts/upgrade/rehearse_upgrade.sh, the same pipeline `make upgrade-rehearse db=<db>` runs on a copy of a real one:
#
#   previous DB state -> drift report -> HTTP smoke (must 500) -> targeted -u -> drift report -> HTTP smoke (must pass)
#
# The previous state is fabricated the way the real one arises: the column that newer code added is absent and the
# module that owns it is recorded at an older version.
DRIFT_COLUMN="nc_must_change_password"

fabricate_previous_state(){       # $1 = db
  local has
  has="$(q "$1" "SELECT count(*) FROM information_schema.columns WHERE table_name='res_users' AND column_name='$DRIFT_COLUMN'")"
  [ "$has" = "1" ] || { no "fixture assumption is stale: res_users.$DRIFT_COLUMN no longer exists - pick another column that newer code adds"; return 1; }
  "${DC[@]}" exec -T db psql -U odoo -d "$1" -c "ALTER TABLE res_users DROP COLUMN $DRIFT_COLUMN" >/dev/null 2>&1
  wind_back "$1" ncollection_core "$CORE_OLD_VERSION"
  [ "$(q "$1" "SELECT count(*) FROM information_schema.columns WHERE table_name='res_users' AND column_name='$DRIFT_COLUMN'")" = "0" ] \
    && ok "previous state fabricated: res_users.$DRIFT_COLUMN absent, ncollection_core recorded at $CORE_OLD_VERSION" \
    || { no "could not fabricate the previous state"; return 1; }
}

install_entry_key(){              # $1 = db: give the fixture what a provisioned tenant has, the platform's service credential
  "${DC[@]}" exec -T odoo odoo shell -d "$1" --no-http --log-level=error "${DBARGS[@]}" >/dev/null 2>&1 <<'PY' || return 1
import base64, hashlib, hmac, os
key = base64.urlsafe_b64encode(hmac.new(
    os.environ["NC_CONFIG_SYNC_KEY"].encode(), b"nc-config-sync:" + env.cr.dbname.encode(), hashlib.sha256).digest()).decode()
env["ncollection.config.sync.key"]._install_key(key, create_user=True)
env.cr.commit()
PY
}

echo "== 6a) GREEN: a database one release behind is detected, reproduced, and cured by a targeted upgrade =="
build_fixture "$DRIFT_DB"
install_entry_key "$DRIFT_DB" && ok "the fixture holds the platform's service credential (a provisioned tenant's state)"   || no "could not install the service credential on the fixture"
if fabricate_previous_state "$DRIFT_DB"; then
  drift_rc=0
  REHEARSE_ENTRY_KEY_DB="$DRIFT_DB" bash "$(pwd)/scripts/upgrade/rehearse_upgrade.sh" --in-place "$DRIFT_DB" --modules ncollection_core >/tmp/upgr_drift_green.log 2>&1 || drift_rc=$?
  [ "$drift_rc" -eq 0 ] && ok "rehearsal PASSED (exit 0)" || { no "rehearsal FAILED (exit $drift_rc) - see /tmp/upgr_drift_green.log"; tail -30 /tmp/upgr_drift_green.log; }
  # H2: the fixture really had live crons, and none was left active before either smoke.
  crons_before="$(sed -n 's/^ *REHEARSAL_CRONS_BEFORE_NEUTRALISE=//p' /tmp/upgr_drift_green.log | head -1 | tr -d '\r')"
  [ "${crons_before:-0}" -gt 0 ] 2>/dev/null \
    && ok "the fixture had $crons_before active cron(s) before the rehearsal (the neutralisation below is not vacuous)" \
    || no "the fixture had no active cron before the rehearsal: the neutralisation proof would be vacuous"
  grep -qa "REHEARSAL_ACTIVE_CRONS=0 (before any Odoo process serves the copy)" /tmp/upgr_drift_green.log \
    && grep -qa "REHEARSAL_RUNNABLE_JOBS=0 (before any Odoo process serves the copy)" /tmp/upgr_drift_green.log \
    && ok "0 active crons and 0 runnable queue jobs BEFORE the first smoke" || no "live work was not proved absent before the first smoke"
  grep -qa "REHEARSAL_ACTIVE_CRONS=0 (after the upgrade)" /tmp/upgr_drift_green.log \
    && ok "0 active crons again right after the upgrade (an upgrade can re-activate data-file crons)" \
    || no "live work was not proved absent after the upgrade"
  [ "$(q "$DRIFT_DB" "SELECT count(*) FROM ir_cron WHERE active")" = "0" ] \
    && ok "the database the rehearsal ran on has 0 active crons at the end" || no "active crons remain on the rehearsed database"
  grep -qa "field res.users.$DRIFT_COLUMN: column missing" /tmp/upgr_drift_green.log \
    && ok "the drift report NAMES the missing column" || no "the drift report did not name res.users.$DRIFT_COLUMN"
  grep -qaE "module ncollection_core: database .* < code" /tmp/upgr_drift_green.log \
    && ok "the drift report names the module that is behind" || no "the drift report did not name ncollection_core as behind"
  # The release gate (ncollection_core/release_gate.py): the stale state that USED to answer 500 here (R-037,
  # `column res_users.nc_must_change_password does not exist`) now gets the controlled maintenance response.
  grep -qaE "\[before\] login page +503" /tmp/upgr_drift_green.log \
    && ok "the stale state is GATED: GET /web/login -> 503 maintenance, not the HTTP 500 it used to answer" \
    || no "the stale state did not answer the 503 maintenance response on /web/login"
  ! grep -qaE "\[before\] [^0-9]+ 500 " /tmp/upgr_drift_green.log \
    && ok "no endpoint of the stale database answered HTTP 500" || no "an endpoint of the stale database still answered 500"
  grep -qa "before upgrade: drift=yes.*smoke=FAIL" /tmp/upgr_drift_green.log \
    && ok "before the upgrade: drift detected AND the HTTP smoke fails" || no "before-upgrade summary is wrong"
  grep -qa "after  upgrade: drift=none, smoke=PASS" /tmp/upgr_drift_green.log \
    && ok "after the targeted upgrade: no drift, every endpoint answers (login page, POST login, web client, session, brand)" \
    || no "after-upgrade summary is wrong"
  # COMPANY ENTRY (the Atlas-eg failure): the platform's grant call is the first thing a stale tenant refuses.
  grep -qaE '\[before\] entry: grant +503' /tmp/upgr_drift_green.log \
    && ok "the stale tenant REFUSES Company Entry cleanly: the grant call answers 503, not the 500 (the Atlas-eg failure)" \
    || no "the stale tenant did not answer the Company Entry grant with the 503 maintenance response"
  entry_ok=1
  for step in 'grant +200 OK' 'redeem token +303 OK' 'tenant Home +200 OK' 'right database +- OK' 'proxy identity +- OK' \
              'returns to platform +- OK' 'signed out +- OK' 'token replay +403 OK'; do
    grep -qaE "\[after\] entry: $step" /tmp/upgr_drift_green.log || { entry_ok=0; echo "     missing after the upgrade: entry: $step"; }
  done
  [ "$entry_ok" = "1" ] \
    && ok "after the targeted upgrade Company Entry works end to end: grant, redeem, tenant Home in the right database as the platform proxy, return, signed out, token single-use" \
    || no "Company Entry did not pass after the targeted upgrade"
  [ "$(q "$DRIFT_DB" "SELECT count(*) FROM information_schema.columns WHERE table_name='res_users' AND column_name='$DRIFT_COLUMN'")" = "1" ] \
    && ok "the column is back, produced by the module's own upgrade (no hand-written SQL)" \
    || no "the column was not restored by the upgrade"
fi

echo "== 6b) RED: an insufficient upgrade must be REFUSED, not waved through =="
if fabricate_previous_state "$DRIFT_DB"; then
  red_rc=0
  bash "$(pwd)/scripts/upgrade/rehearse_upgrade.sh" --in-place "$DRIFT_DB" --modules ncollection_auth >/tmp/upgr_drift_red.log 2>&1 || red_rc=$?
  [ "$red_rc" -ne 0 ] && ok "rehearsal FAILED CLOSED (exit $red_rc) when the wrong module was upgraded" \
    || no "rehearsal PASSED although ncollection_core was never upgraded - the gate is blind"
  grep -qa "REHEARSAL=FAIL" /tmp/upgr_drift_red.log && ok "verdict is REHEARSAL=FAIL" || no "no REHEARSAL=FAIL verdict"
  grep -qa "drift remains after the upgrade" /tmp/upgr_drift_red.log \
    && ok "it says WHY: drift remains after the upgrade" || no "it failed without naming the remaining drift"
fi

echo "== 6c) the detector's own refusals =="
if bash "$(pwd)/scripts/upgrade/rehearse_upgrade.sh" --in-place "$DRIFT_DB" --modules all >/dev/null 2>&1; then
  no "'--modules all' was accepted: the gate must never run -u all"
else ok "'--modules all' is refused"; fi
if bash "$(pwd)/scripts/upgrade/rehearse_upgrade.sh" --in-place ncollection >/dev/null 2>&1; then
  no "--in-place accepted the real 'ncollection' database"
else ok "--in-place refuses any database that is not a scratch upgr* one"; fi
REHEARSE="$(pwd)/scripts/upgrade/rehearse_upgrade.sh"
modules_state="$(q "$DRIFT_DB" "SELECT md5(string_agg(name||state||coalesce(latest_version,''), ',' ORDER BY name)) FROM ir_module_module")"
refused(){        # $1 = label ; $2 = text the refusal must contain ; rest = rehearsal arguments. Exit 2, nothing done.
  local label="$1" why="$2" rc=0; shift 2
  bash "$REHEARSE" "$@" >/tmp/upgr_refusal.log 2>&1 || rc=$?
  if [ "$rc" -eq 2 ] && grep -qa -- "$why" /tmp/upgr_refusal.log; then ok "$label (exit 2)"
  else no "$label: exit $rc, expected 2 with '$why'"; tail -5 /tmp/upgr_refusal.log; fi
}
# M7: --in-place is an exact allow-list of this suite's fixtures, never a prefix.
refused "--in-place refuses a real-looking tenant that merely starts with upgr" "own fixtures" --in-place upgradelogistics
refused "--in-place refuses a near-miss of a fixture name" "own fixtures" --in-place upgrdrift2
refused "--in-place refuses a rehearsal scratch name" "own fixtures" --in-place "$COLLIDE_DB"
refused "--in-place refuses a mixed-case fixture name" "plain lowercase" --in-place UPGRDRIFT
# L1: a source name that could collide once mapped to a scratch name, or a scratch copy as a source.
refused "a mixed-case source is refused" "plain lowercase" Upgrdrift
refused "a source with an underscore is refused" "plain lowercase" upgr_drift
refused "a rehearsal scratch copy is never a source" "never a source" "$COLLIDE_DB"
# M2: the module list follows the ONE rule the preflight uses, also when it is given explicitly.
refused "'--modules base' is refused (it would re-load every module)" "never upgraded by this tooling" --in-place "$DRIFT_DB" --modules base
refused "an Odoo core module outside the NCollection / OCA roots is refused" "not an NCollection or OCA module" --in-place "$DRIFT_DB" --modules ncollection_core,web
refused "a module whose code is not deployed is refused" "not an NCollection or OCA module" --in-place "$DRIFT_DB" --modules ncollection_core,ncollection_not_deployed
[ "$(q "$DRIFT_DB" "SELECT md5(string_agg(name||state||coalesce(latest_version,''), ',' ORDER BY name)) FROM ir_module_module")" = "$modules_state" ] \
  && ok "no refusal upgraded anything: the module table is byte-identical" || no "a refused rehearsal changed the module table"
# L1: a scratch database that already exists is refused, never dropped or reused.
"${DC[@]}" exec -T db createdb -U odoo "$COLLIDE_DB" >/dev/null 2>&1
refused "a copy-mode rehearsal refuses when its scratch database already exists" "already exists" "$DRIFT_DB"
[ "$(q postgres "SELECT count(*) FROM pg_database WHERE datname='$COLLIDE_DB'")" = "1" ] \
  && ok "the pre-existing '$COLLIDE_DB' was NOT dropped" || no "the rehearsal dropped a database it did not create"
drop_db "$COLLIDE_DB"
# L1: one rehearsal at a time; the lock of another run is refused, not broken.
if "${DC[@]}" exec -T odoo sh -c 'mkdir /tmp/nc_rehearse_upgrade.lock'; then
  refused "a second rehearsal is refused while another holds the lock" "another upgrade rehearsal" --in-place "$DRIFT_DB"
  "${DC[@]}" exec -T odoo sh -c 'test -d /tmp/nc_rehearse_upgrade.lock' \
    && ok "the other run's lock was left in place" || no "the refused rehearsal removed another run's lock"
  "${DC[@]}" exec -T odoo sh -c 'rmdir /tmp/nc_rehearse_upgrade.lock'
else
  no "a rehearsal lock already exists in the odoo container: is a rehearsal running? (the lock arm cannot run)"
fi

echo "== 6d) COPY mode: the copy is neutralised in the same transaction as its data; the source is only read =="
# A copy of a real database (the release path: make upgrade-rehearse db=<db>) carries live crons. Give the fixture
# active crons that are NOT due (next call pushed out, so the shared server still runs nothing on it), copy it, and
# require that the COPY never had an active cron while the SOURCE keeps every one.
"${DC[@]}" exec -T db psql -U odoo -d "$DRIFT_DB" -qc "UPDATE ir_cron SET active = true, nextcall = '9999-12-31'" >/dev/null 2>&1
src_active="$(q "$DRIFT_DB" "SELECT count(*) FROM ir_cron WHERE active")"
src_state="$(q "$DRIFT_DB" "SELECT md5(string_agg(id||active::text||nextcall::text, ',' ORDER BY id)) FROM ir_cron")"
copy_rc=0; bash "$REHEARSE" "$DRIFT_DB" >/tmp/upgr_drift_copy.log 2>&1 || copy_rc=$?
[ "$copy_rc" -eq 0 ] && ok "a copy-mode rehearsal of the stale fixture PASSES (exit 0)" \
  || { no "the copy-mode rehearsal FAILED (exit $copy_rc) - see /tmp/upgr_drift_copy.log"; tail -30 /tmp/upgr_drift_copy.log; }
[ "${src_active:-0}" -gt 0 ] 2>/dev/null && grep -qa "REHEARSAL_SOURCE_ACTIVE_CRONS=$src_active " /tmp/upgr_drift_copy.log \
  && ok "the source had $src_active active cron(s) when it was copied (not vacuous)" || no "the source had no active cron when copied"
grep -qa "REHEARSAL_ACTIVE_CRONS_AT_RESTORE=0" /tmp/upgr_drift_copy.log \
  && ok "the copy had 0 active crons the moment its restore committed (one transaction)" \
  || no "the copy was visible with active crons after its restore"
grep -qa "REHEARSAL_ACTIVE_CRONS=0 (before any Odoo process serves the copy)" /tmp/upgr_drift_copy.log \
  && grep -qa "REHEARSAL_ACTIVE_CRONS=0 (after the upgrade)" /tmp/upgr_drift_copy.log \
  && ok "0 active crons before the first smoke and again after the upgrade" || no "live work was not proved absent on the copy"
grep -qa "upgrading ONLY: ncollection_core" /tmp/upgr_drift_copy.log \
  && ok "the automatic list is exactly the preflight's: ncollection_core" || no "the automatic module list was not the preflight's"
[ "$(q "$DRIFT_DB" "SELECT md5(string_agg(id||active::text||nextcall::text, ',' ORDER BY id)) FROM ir_cron")" = "$src_state" ] \
  && ok "the SOURCE's crons are untouched (only read)" || no "the rehearsal changed the source's crons"
[ "$(q postgres "SELECT count(*) FROM pg_database WHERE datname='$COLLIDE_DB'")" = "0" ] \
  && ! "${DC[@]}" exec -T odoo sh -c "test -e /var/lib/odoo/filestore/$COLLIDE_DB" \
  && ok "the copy and its filestore were dropped by the run that created them" || no "the copy or its filestore was left behind"
"${DC[@]}" exec -T db psql -U odoo -d "$DRIFT_DB" -qc "UPDATE ir_cron SET active = false" >/dev/null 2>&1

# =========================================================================
echo "== 7) PREFLIGHT (R-037): the read-only release gate fails closed on a stale database and passes a current one =="
# =========================================================================
# The rehearsal proves a cure on a COPY. This is the cheap gate that runs against the REAL database before traffic is
# pointed at new code: it compares each installed module's recorded version with the code's, opens no registry, writes
# nothing. DRIFT_DB is stale here (6b left it that way) and is upgraded properly below.
PREFLIGHT="$(pwd)/scripts/upgrade/preflight_versions.sh"
before_state="$(q "$DRIFT_DB" "SELECT md5(string_agg(name||latest_version, ',' ORDER BY name)) FROM ir_module_module")"
pre_rc=0; bash "$PREFLIGHT" "$DRIFT_DB" >/tmp/upgr_preflight_stale.log 2>&1 || pre_rc=$?
[ "$pre_rc" -eq 1 ] && ok "a stale database FAILS the preflight (exit 1)" || no "a stale database did not fail the preflight (exit $pre_rc)"
grep -qa "PREFLIGHT_BEHIND=ncollection_core database .* < code" /tmp/upgr_preflight_stale.log \
  && ok "it names the module that is behind, with both versions" || no "the preflight did not name ncollection_core"
[ "$(q "$DRIFT_DB" "SELECT md5(string_agg(name||latest_version, ',' ORDER BY name)) FROM ir_module_module")" = "$before_state" ] \
  && ok "it is READ-ONLY: the module table is byte-identical after the check" || no "the preflight changed the database"
if upgrade "$DRIFT_DB" ncollection_core >/tmp/upgr_preflight_upgrade.log 2>&1; then
  pre_rc=0; bash "$PREFLIGHT" "$DRIFT_DB" >/tmp/upgr_preflight_current.log 2>&1 || pre_rc=$?
  [ "$pre_rc" -eq 0 ] && ok "after the targeted upgrade the preflight PASSES (exit 0)" || no "the preflight still fails after the upgrade (exit $pre_rc)"
else
  no "the targeted upgrade of ncollection_core failed - see /tmp/upgr_preflight_upgrade.log"
fi
pre_rc=0; bash "$PREFLIGHT" upgrnosuchdb >/dev/null 2>&1 || pre_rc=$?
[ "$pre_rc" -eq 2 ] && ok "a database it cannot reach is exit 2, never a silent pass" || no "a missing database gave exit $pre_rc instead of 2"
pre_rc=0; bash "$PREFLIGHT" 'bad;name' >/dev/null 2>&1 || pre_rc=$?
[ "$pre_rc" -eq 2 ] && ok "a database name that is not plain is refused (exit 2)" || no "an unsafe name gave exit $pre_rc instead of 2"

# =========================================================================
echo "== 8) RELEASE GATE: a stale tenant never answers HTTP 500, and only the platform opens it again =="
# =========================================================================
# ncollection_core/release_gate.py, served by a REAL server (Odoo's own test server runs with the gate off). The
# server routes by host (db_filter=^%d$), like the edge, so a stale tenant and a healthy one are served side by side.
# DRIFT_DB is current here (arm 7 upgraded it) and holds the platform's service credential (6a); GREEN_DB is the
# healthy neighbour. The stale states are fabricated the way they arise (module recorded older, column absent).
GATE_PORT=8199
GATE_LOG=/tmp/upgr_gate_server.log
STATUS_PATH=/ncollection/release/status
HOLD_PATH=/ncollection/release/hold
OPEN_PATH=/ncollection/release/open
gate_server_stop(){ "${DC[@]}" exec -T odoo sh -c "pkill -f 'http-port=$GATE_PORT' 2>/dev/null; sleep 1; pkill -9 -f 'http-port=$GATE_PORT' 2>/dev/null; true" >/dev/null 2>&1 || true; }
probe(){          # host-db method path [bearer-db] [json] -> prints the GATE line
  # The path is an ARGUMENT starting with '/': Git Bash would rewrite it into a Windows path without these.
  MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL="*" \
  "${DC[@]}" exec -T odoo python3 - "http://127.0.0.1:$GATE_PORT" "$1.localhost" "$2" "$3" "${4:--}" "${5:--}" \
    < scripts/upgrade/gate_probe.py 2>&1 | grep -a '^GATE ' | head -1
}
field(){ printf '%s\n' "$1" | sed -n "s/.* $2=\([^ ]*\).*/\1/p"; }
modules_md5(){ q "$1" "SELECT md5(string_agg(name||state||coalesce(latest_version,''), ',' ORDER BY name)) FROM ir_module_module"; }
settle(){ sleep 6; }   # a pause between fabricating a state and probing it (the gate itself re-reads a token per request)

gate_server_stop
MSYS_NO_PATHCONV=1 "${DC[@]}" exec -d odoo sh -c "export PGHOST=\"\$HOST\" PGUSER=\"\$USER\" PGPASSWORD=\"\$PASSWORD\"; exec odoo --http-port=$GATE_PORT --gevent-port=8198 --http-interface=127.0.0.1 --max-cron-threads=0 --db-filter='^%d\$' --no-database-list --log-level=warn > $GATE_LOG 2>&1"
up=0
for _ in $(seq 1 45); do
  [ "$(field "$(probe "$DRIFT_DB" GET /web/health)" status)" = "200" ] && { up=1; break; }; sleep 2
done
if [ "$up" -ne 1 ]; then
  no "the gate server did not come up"; "${DC[@]}" exec -T odoo sh -c "tail -20 $GATE_LOG" | sed 's/^/     | /'
else
  r="$(probe "$DRIFT_DB" GET /web/login)"; g="$(probe "$GREEN_DB" GET /web/login)"
  [ "$(field "$r" status)" = "200" ] && [ "$(field "$g" status)" = "200" ] \
    && ok "baseline: two READY tenants answer their sign-in page (200)" || no "baseline is not READY: $r / $g"
  r="$(probe "$DRIFT_DB" GET "$STATUS_PATH")"
  [ "$(field "$r" status)" = "404" ] && ok "the release status is invisible without the platform's bearer (404)" || no "status without bearer: $r"
  r="$(probe "$DRIFT_DB" GET "$STATUS_PATH" "$GREEN_DB")"
  [ "$(field "$r" status)" = "404" ] && ok "another tenant's bearer is refused (404): the key is per tenant" || no "foreign bearer: $r"

  echo "  -- CASE A: one module behind (the R-037 state that answered 500)"
  fabricate_previous_state "$DRIFT_DB" >/dev/null; settle
  before_mods="$(modules_md5 "$DRIFT_DB")"
  r="$(probe "$DRIFT_DB" GET /web/login)"
  [ "$(field "$r" status)" = "503" ] && [ "$(field "$r" state)" = "upgrade_required" ] \
    && ok "GET /web/login -> 503, X-NC-Workspace-State: upgrade_required (was HTTP 500)" || no "case A sign-in page: $r"
  [ "$(field "$r" traceback)" = "0" ] && [ "$(field "$r" internal_error)" = "0" ] \
    && ok "no traceback and no 'Internal Server Error' in what the tenant sees" || no "case A leaked: $r"
  r="$(probe "$DRIFT_DB" POST /web/session/get_session_info - '{}')"
  [ "$(field "$r" status)" = "503" ] && printf '%s' "$r" | grep -q 'workspace_unavailable' \
    && ok "a JSON client gets a JSON 503 (no HTML, no traceback)" || no "case A json: $r"
  r="$(probe "$DRIFT_DB" GET "$STATUS_PATH" "$DRIFT_DB")"
  printf '%s' "$r" | grep -q '"state":"upgrade_required"' && printf '%s' "$r" | grep -q '\["ncollection_core",' \
    && ok "the platform sees the exact drift: ncollection_core, database version < code version" || no "case A status: $r"
  r="$(probe "$GREEN_DB" GET /web/login)"
  [ "$(field "$r" status)" = "200" ] && ok "the healthy neighbour keeps serving (no global outage)" || no "neighbour: $r"
  [ "$(modules_md5 "$DRIFT_DB")" = "$before_mods" ] \
    && ok "no request upgraded anything: the module table is byte-identical" || no "a request changed the module table"

  echo "  -- CASE B: several modules behind"
  wind_back "$DRIFT_DB" ncollection_branding 19.0.1.8.0; wind_back "$DRIFT_DB" ncollection_auth 19.0.1.2.0; settle
  before_mods="$(modules_md5 "$DRIFT_DB")"
  r="$(probe "$DRIFT_DB" GET /atlas)"
  [ "$(field "$r" status)" = "503" ] && ok "GET /atlas -> 503 (several modules behind)" || no "case B: $r"
  r="$(probe "$DRIFT_DB" GET "$STATUS_PATH" "$DRIFT_DB")"
  for m in ncollection_auth ncollection_branding ncollection_core; do
    printf '%s' "$r" | grep -q "\[\"$m\"," || { no "case B status does not name $m"; }
  done
  printf '%s' "$r" | grep -q '\["ncollection_auth",' && printf '%s' "$r" | grep -q '\["ncollection_branding",' \
    && ok "the platform sees all three modules behind, to be upgraded together" || true
  [ "$(modules_md5 "$DRIFT_DB")" = "$before_mods" ] && ok "no partial upgrade happened" || no "case B: the module table changed"

  echo "  -- CASE E: Company Entry into the stale tenant"
  r="$(probe "$DRIFT_DB" POST /json/2/ncollection.platform.entry/grant_from_platform "$DRIFT_DB" '{"vals": {"token_hash": "x"}}')"
  [ "$(field "$r" status)" = "503" ] && ok "the platform's grant call gets 503 (no grant, no token into a broken tenant)" || no "case E grant: $r"
  r="$(probe "$DRIFT_DB" POST /ncollection/platform/enter - '{}')"
  [ "$(field "$r" status)" = "503" ] && [ "$(field "$r" traceback)" = "0" ] \
    && ok "redeeming an entry on the stale tenant gets 503, never a 500 or a session" || no "case E redeem: $r"
  r="$(probe "$DRIFT_DB" POST "$OPEN_PATH" "$DRIFT_DB" '{}')"
  [ "$(field "$r" status)" = "409" ] && ok "the platform cannot open a tenant the code is ahead of (409)" || no "open while stale: $r"

  echo "  -- CASE C: a failed tenant stays held, the others keep serving"
  r="$(probe "$DRIFT_DB" POST "$HOLD_PATH" "$DRIFT_DB" '{"hold": "upgrade_failed", "reason": "suite"}')"
  printf '%s' "$r" | grep -q '"state":"upgrade_failed"' && ok "held as upgrade_failed by the platform" || no "case C hold: $r"
  r="$(probe "$DRIFT_DB" GET /web/login)"; g="$(probe "$GREEN_DB" GET /web/login)"
  [ "$(field "$r" state)" = "upgrade_failed" ] && [ "$(field "$g" status)" = "200" ] \
    && ok "the failed tenant answers 503 upgrade_failed while the READY tenant answers 200" || no "case C: $r / $g"

  echo "  -- CASE F: the exact targeted upgrade, validated, then opened"
  if upgrade "$DRIFT_DB" ncollection_core,ncollection_branding,ncollection_auth >/tmp/upgr_gate_upgrade.log 2>&1; then
    ok "targeted upgrade of exactly the three modules the gate named (one run)"
  else no "the targeted upgrade failed - see /tmp/upgr_gate_upgrade.log"; fi
  settle
  r="$(probe "$DRIFT_DB" GET /web/login)"
  [ "$(field "$r" status)" = "503" ] && ok "after the upgrade it is STILL closed until the platform opens it (held)" || no "case F held: $r"
  probe "$DRIFT_DB" POST "$HOLD_PATH" "$DRIFT_DB" '{"hold": "upgrading"}' >/dev/null
  r="$(probe "$DRIFT_DB" GET /web/login "$DRIFT_DB")"; u="$(probe "$DRIFT_DB" GET /web/login)"
  [ "$(field "$r" status)" = "200" ] && [ "$(field "$u" status)" = "503" ] \
    && ok "while held for validation, the platform's bearer gets the real page and nobody else does" || no "case F validation: $r / $u"
  r="$(probe "$DRIFT_DB" POST "$OPEN_PATH" "$DRIFT_DB" '{}')"
  printf '%s' "$r" | grep -q '"state":"ready"' && ok "opened: READY" || no "case F open: $r"
  r="$(probe "$DRIFT_DB" GET /web/login)"
  [ "$(field "$r" status)" = "200" ] && [ "$(field "$r" state)" = "-" ] \
    && ok "normal sign-in page again (200, no maintenance header)" || no "case F login: $r"
  r="$(probe "$DRIFT_DB" POST /json/2/ncollection.platform.entry/grant_from_platform "$DRIFT_DB" '{"vals": {"token_hash": "'"$(printf 'f%.0s' $(seq 1 64))"'", "platform_ref": "u7", "platform_name": "Suite", "return_url": "https://platform.invalid/odoo", "ttl": 60}}')"
  [ "$(field "$r" status)" = "200" ] && ok "Company Entry works again: the platform's grant call is accepted (200)" || no "case F entry grant: $r"
  srv_tb="$("${DC[@]}" exec -T odoo sh -c "grep -ac 'Traceback (most recent call last)' $GATE_LOG || true" | tr -d '[:space:]')"
  [ "${srv_tb:-0}" = "0" ] && ok "the server logged no traceback during the whole arm" || no "the server logged $srv_tb traceback(s)"
  "${DC[@]}" exec -T odoo sh -c "grep -a 'Release gate:' $GATE_LOG | head -8" | sed 's/^/     | /'
fi
gate_server_stop

# =========================================================================
echo "== 9) CRON GATE: a database the code is ahead of runs no background work; its READY neighbour keeps running =="
# =========================================================================
# The HTTP gate (arm 8) never sees crons: a serving process with no -d runs EVERY database's crons
# (cron_database_list() = list_dbs(True)). cron_gate_probe.py boots like a server (server-wide modules -> the gate's
# post_load) and makes the cron runner's own call, IrCron._process_jobs(db), with Odoo's real cron code against these
# fixtures - no fake, no patch. Each fixture gets a PROBE CRON (not due); the probe makes it due right before the call
# and reads back whether it ran. DRIFT_DB and GREEN_DB are both READY here (arm 8 opened DRIFT_DB again).
PROBE_CRON='NC cron gate probe'
PROBE_KEY=ncollection.cronprobe.ran
CRON_NEW_DB=upgrcronnew           # a database being created: no module table yet
add_probe_cron(){                 # $1 = db
  "${DC[@]}" exec -T odoo odoo shell -d "$1" --no-http --log-level=error "${DBARGS[@]}" >/dev/null 2>&1 <<PY || return 1
env['ir.cron'].create({'name': '$PROBE_CRON', 'model_id': env.ref('base.model_ir_config_parameter').id,
                       'state': 'code', 'code': "env['ir.config_parameter'].sudo().set_param('$PROBE_KEY', 'yes')",
                       'interval_number': 1, 'interval_type': 'days', 'nextcall': '2999-01-01 00:00:00', 'active': True})
env.cr.commit()
PY
  [ "$(q "$1" "SELECT count(*) FROM ir_cron WHERE active AND cron_name='$PROBE_CRON'")" = "1" ]
}
cron_probe(){                     # db... -> the CRON lines of ONE probe process, in order
  MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL="*" \
  "${DC[@]}" exec -T odoo python3 - "${DBARGS[@]}" -- "$@" < scripts/upgrade/cron_gate_probe.py 2>>/tmp/upgr_cron_probe.log \
    | grep -a '^CRON' || true          # nothing printed is reported by the assertions below, never set -e
}
line_of(){ printf '%s\n' "$1" | grep -a "^CRON db=$2 " | head -1 || true; }
set_hold(){                       # $1 = db ; $2 = hold value, or '' to clear (what /hold and /open write)
  if [ -n "$2" ]; then
    "${DC[@]}" exec -T db psql -U odoo -d "$1" -qc "INSERT INTO ir_config_parameter (key, value) VALUES ('ncollection.release.hold', '$2') ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value" >/dev/null 2>&1
  else
    "${DC[@]}" exec -T db psql -U odoo -d "$1" -qc "DELETE FROM ir_config_parameter WHERE key = 'ncollection.release.hold'" >/dev/null 2>&1
  fi
}
: > /tmp/upgr_cron_probe.log
drop_db "$CRON_NEW_DB"
if add_probe_cron "$DRIFT_DB" && add_probe_cron "$GREEN_DB"; then
  out="$(cron_probe)"
  printf '%s' "$out" | grep -qa '^CRON_GATE installed=1 cron_seam=-$' \
    && ok "the probe booted like a server: the cron gate is installed by the server-wide post_load, seam valid" \
    || no "the cron gate is not installed in a server-like process: $out"

  echo "  -- baseline: both READY, both run (the probe is not vacuous)"
  out="$(cron_probe "$DRIFT_DB" "$GREEN_DB")"
  for db in "$DRIFT_DB" "$GREEN_DB"; do
    l="$(line_of "$out" "$db")"
    printf '%s' "$l" | grep -qa 'gate=ready registry_before=0 registry_after=1 ran=1' \
      && ok "$db READY: its registry loaded through the cron runner and the probe job ran" || no "baseline $db: $l"
  done

  echo "  -- stale tenant A next to READY tenant B, in ONE cron process (A polled first)"
  fabricate_previous_state "$DRIFT_DB" || true
  out="$(cron_probe "$DRIFT_DB" "$GREEN_DB")"
  l="$(line_of "$out" "$DRIFT_DB")"
  printf '%s' "$l" | grep -qa 'gate=upgrade_required registry_before=0 registry_after=0 ran=0' \
    && ok "stale $DRIFT_DB: NO registry loaded and NO job executed by the cron runner" || no "stale cron: $l"
  l="$(line_of "$out" "$GREEN_DB")"
  printf '%s' "$l" | grep -qa 'gate=ready registry_before=0 registry_after=1 ran=1' \
    && ok "READY $GREEN_DB keeps running its crons in the same process right after the stale one" || no "neighbour: $l"

  echo "  -- every other non-READY state, on the READY neighbour"
  for hold in upgrading upgrade_failed blocked provisioning; do
    set_hold "$GREEN_DB" "$hold"
    l="$(line_of "$(cron_probe "$GREEN_DB")" "$GREEN_DB")"
    printf '%s' "$l" | grep -qa "gate=$hold registry_before=0 registry_after=0 ran=0" \
      && ok "held $hold (compatible, so HTTP-passable for the platform): no registry, no job" || no "hold $hold: $l"
  done
  set_hold "$GREEN_DB" ''
  "${DC[@]}" exec -T db psql -U odoo -d "$GREEN_DB" -qc "UPDATE ir_module_module SET state='to upgrade' WHERE name='ncollection_auth'" >/dev/null 2>&1
  l="$(line_of "$(cron_probe "$GREEN_DB")" "$GREEN_DB")"
  "${DC[@]}" exec -T db psql -U odoo -d "$GREEN_DB" -qc "UPDATE ir_module_module SET state='installed' WHERE name='ncollection_auth'" >/dev/null 2>&1
  printf '%s' "$l" | grep -qa "gate=upgrading registry_before=0 registry_after=0 ran=0" \
    && ok "a module mid-upgrade ('to upgrade' row): no registry, no job" || no "to upgrade: $l"
  "${DC[@]}" exec -T db createdb -U odoo -O odoo "$CRON_NEW_DB" >/dev/null 2>&1
  l="$(line_of "$(cron_probe "$CRON_NEW_DB")" "$CRON_NEW_DB")"
  printf '%s' "$l" | grep -qa "gate=provisioning registry_before=0 registry_after=0 ran=-" \
    && ok "a database being created (no module table): provisioning, no registry, nothing polled" || no "new db: $l"
  ! grep -qa "Tried to poll an undefined table on database $CRON_NEW_DB" /tmp/upgr_cron_probe.log \
    && ok "Odoo's cron code never touched it (the gate decided first)" || no "Odoo's cron code polled the new database"
  drop_db "$CRON_NEW_DB"
  l="$(line_of "$(cron_probe "$GREEN_DB")" "$GREEN_DB")"
  printf '%s' "$l" | grep -qa 'gate=ready registry_before=0 registry_after=1 ran=1' \
    && ok "back to READY (hold cleared, rows installed): crons run again" || no "resume: $l"
  ctb="$(grep -ac 'Traceback (most recent call last)\|Exception in cron' /tmp/upgr_cron_probe.log || true)"
  [ "${ctb:-0}" = "0" ] && ok "no traceback and no 'Exception in cron' in any probe process" \
    || { no "the probe processes logged $ctb exception(s)"; tail -20 /tmp/upgr_cron_probe.log | sed 's/^/     | /'; }
  grep -a 'Release gate: db=' /tmp/upgr_cron_probe.log | head -6 | sed 's/^/     | /'
else
  no "could not give the fixtures their probe cron"
fi

hr
echo "SUMMARY: $pass passed, $fail failed."
[ "$fail" -eq 0 ] || exit 1
echo "✅ Upgrades run their migrations, data survives them, and code-ahead-of-database drift is caught before it can 500, and the release preflight fails closed."
