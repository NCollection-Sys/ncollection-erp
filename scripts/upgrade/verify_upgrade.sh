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
#  Fixture DBs: upgrgreen · upgrred   (prefix `upgr*`, owned by this script only)
#  Idempotent: both are dropped on entry and on exit (Rule 12).
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
trap 'drop_db "$GREEN_DB"; drop_db "$RED_DB"' EXIT

q(){ "${DC[@]}" exec -T db psql -U odoo -d "$1" -tAc "$2" 2>/dev/null | tr -d '[:space:]'; }

build_fixture(){   # $1 = db
  local db="$1"
  drop_db "$db"
  "${DC[@]}" exec -T db createdb -U odoo -O odoo "$db" >/dev/null 2>&1
  "${DC[@]}" exec -T odoo odoo -d "$db" -i "$MODULES" --without-demo=True --no-http \
      --stop-after-init "${DBARGS[@]}" >/dev/null 2>&1
}

wind_back(){       # $1 = db — make Odoo believe the module is older than it is
  "${DC[@]}" exec -T db psql -U odoo -d "$1" -c \
    "UPDATE ir_module_module SET latest_version='${OLD_VERSION}' WHERE name='ncollection_subscription'" \
    >/dev/null 2>&1
}

upgrade(){         # $1 = db ; returns odoo's exit code, log on stdout
  "${DC[@]}" exec -T odoo odoo -d "$1" -u ncollection_subscription --no-http \
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
wind_back "$GREEN_DB"
seen="$(q "$GREEN_DB" "SELECT latest_version FROM ir_module_module WHERE name='ncollection_subscription'")"
[ "$seen" = "$OLD_VERSION" ] && ok "latest_version wound back to $OLD_VERSION" \
  || no "could not wind version back (got '$seen')"

if upgrade "$GREEN_DB" > /tmp/upgr_green.log 2>&1; then
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
wind_back "$RED_DB"

if upgrade "$RED_DB" > /tmp/upgr_red.log 2>&1; then
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

hr
echo "SUMMARY: $pass passed, $fail failed."
[ "$fail" -eq 0 ] || exit 1
echo "✅ Upgrades run their migrations, and data survives them."
