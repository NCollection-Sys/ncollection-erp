#!/usr/bin/env bash
# shellcheck disable=SC2016  # single-quoted scripts expand in the CONTAINER's shell
# ============================================================================
#  The PLATFORM release, end to end, on a scratch COPY of the platform database (release gate, R-037)
# ============================================================================
#  NEW CODE + OLD PLATFORM DB -> controlled pre-registry 503 -> the operator reads the exact incompatibility -> the
#  exact migration runs on the copy -> registry + drift validation -> held HTTP smoke -> open -> READY -> normal traffic.
#
#   1  copy <source> (default ncollection) -> upgrplat with pg_dump (the source is only READ), neutralised in the
#      restore transaction (scripts/upgrade/neutralize_copy.sql) and again by Odoo's own neutralize
#   2  the copy's modules behind the code (preflight, read-only) - and EXPECT_MODULES if given, exactly
#   3  a release-gated scratch server (server-wide ncollection_core from the config, NO -d: nothing preloads a registry,
#      127.0.0.1 only, no crons)
#   4  GET /web/login -> 503 upgrade_required, the maintenance page, no traceback, no registry loaded, the copy's state
#      unchanged, and the gate's reasoned line in the server's own log
#   5  the operator status (scripts/upgrade/release_status.py): role platform, the exact modules, fingerprints; refused
#      (404) without the bearer and with another database's bearer
#   6  the release plan (scripts/upgrade/release_plan.sh) names the same modules
#   7  the rehearsal of <source> (scripts/upgrade/rehearse_upgrade.sh) passes and names the same modules
#   8  hold upgrading -> -u EXACTLY those modules on the copy -> setup-log guard -> drift report clean, preflight PASS
#   9  held smoke: the operator bearer gets 200, everyone else 503; Fleet Migration refuses (platform not READY)
#  10  open -> READY, last successful release recorded, /web/login 200 without a bearer, the HTTP smoke passes, Fleet
#      Migration's platform-first check passes, no module outside the list changed version
#  11  the source's state fingerprint is unchanged
#
#  Owns upgrplat (CLAUDE.md fixture table); refuses if it exists, drops it on every exit path. Never touches DNS, never
#  writes the source, never runs -u all.   usage: scripts/upgrade/verify_platform_release.sh [source_db]
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")/../.."
export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL="*"

DC=(docker compose)
SOURCE="${1:-ncollection}"
SCRATCH=upgrplat
PORT=8196
GEVENT_PORT=8195
BASE="http://127.0.0.1:$PORT"
RUN_ID="$(date +%Y%m%d%H%M%S)_$$"
SERVER_LOG="/tmp/upgrplat_server_${RUN_ID}.log"      # inside the odoo container
LOG_DIR="$(mktemp -d)"
ADMIN_PW="$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"
fail=0
CREATED=0
FS_CREATED=0
SERVER=0

case "$SOURCE" in *[!a-z0-9]*|"") echo "REFUSING: '$SOURCE' is not a plain lowercase alphanumeric name" >&2; exit 2 ;; esac
[ "$SOURCE" != "$SCRATCH" ] || { echo "REFUSING: the source cannot be the scratch copy" >&2; exit 2; }

in_odoo(){
  "${DC[@]}" exec -T odoo sh -c '[ -n "${HOST:-}" ] && [ -n "${USER:-}" ] && [ -n "${PASSWORD:-}" ] \
    || { echo "NO_DB_CREDENTIALS: the odoo container has no HOST / USER / PASSWORD" >&2; exit 97; }
    export PGHOST="$HOST" PGUSER="$USER" PGPASSWORD="$PASSWORD"; exec "$@"' _ "$@"
}
in_db(){ "${DC[@]}" exec -T db sh -c 'export PGUSER="$POSTGRES_USER"; exec "$@"' _ "$@"; }
say(){ printf '%s\n' "$*"; }
step(){ say ""; say "== $* =="; }
bad(){ say "  ❌ $*"; fail=1; }
good(){ say "  ✅ $*"; }
check(){ if [ "$2" = "$3" ]; then good "$1 ($2)"; else bad "$1: expected '$3', got '${2:-<empty>}'"; fi; }
marker(){ printf '%s\n' "$1" | sed -n "s/^$2=//p" | head -1 | tr -d '\r'; }
field(){ printf '%s\n' "$1" | sed -n "s/.* $2=\([^ ]*\).*/\1/p" | tr -d '\r'; }
q(){ in_db psql -d "$1" -Atc "$2" | tr -d '\r'; }
db_exists(){ [ "$(q postgres "SELECT count(*) FROM pg_database WHERE datname='$1'")" = "1" ]; }
# Modules, every parameter (the three release keys included), every user row's write: what "unchanged" means here.
state_fp(){ q "$1" "SELECT md5((SELECT string_agg(name||state||coalesce(latest_version,''), ',' ORDER BY name) FROM ir_module_module) || (SELECT coalesce(string_agg(key||coalesce(value,''), ',' ORDER BY key),'') FROM ir_config_parameter) || (SELECT string_agg(id||coalesce(write_date::text,''), ',' ORDER BY id) FROM res_users))"; }
versions(){ q "$1" "SELECT name||'='||coalesce(latest_version,'') FROM ir_module_module WHERE state='installed' ORDER BY name"; }
probe(){   # host-db method path [bearer-db] -> the GATE line
  "${DC[@]}" exec -T odoo python3 - "$BASE" "$1.localhost" "$2" "$3" "${4:--}" - < scripts/upgrade/gate_probe.py 2>&1 \
    | grep -a '^GATE ' | head -1
}
release(){ "${DC[@]}" exec -T odoo python3 - "$BASE" "$SCRATCH" "$@" < scripts/upgrade/release_status.py 2>&1 || true; }
server_log(){ "${DC[@]}" exec -T odoo sh -c 'cat "$1" 2>/dev/null' _ "$SERVER_LOG"; }
shell_run(){ in_odoo odoo shell -d "$SCRATCH" --no-http --log-level=error 2>&1; }
neutralise(){ in_db psql -q -1 -v ON_ERROR_STOP=1 -d "$SCRATCH" < scripts/upgrade/neutralize_copy.sql >/dev/null; }

# shellcheck disable=SC2329  # stop_server and cleanup run from the EXIT trap
stop_server(){
  "${DC[@]}" exec -T odoo sh -c "pkill -f 'http-port=$PORT' 2>/dev/null; sleep 1; pkill -9 -f 'http-port=$PORT' 2>/dev/null; true" >/dev/null 2>&1 || true
}
# shellcheck disable=SC2329  # the EXIT trap below
cleanup(){
  [ "$SERVER" -eq 1 ] && stop_server
  [ "$SERVER" -eq 1 ] && { "${DC[@]}" exec -T odoo sh -c 'rm -f "$1"' _ "$SERVER_LOG" >/dev/null 2>&1 || true; }
  if [ "$CREATED" -eq 1 ]; then
    q postgres "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$SCRATCH' AND pid<>pg_backend_pid()" >/dev/null 2>&1 || true
    in_db dropdb --if-exists "$SCRATCH" >/dev/null 2>&1 || true
  fi
  [ "$FS_CREATED" -eq 1 ] && { "${DC[@]}" exec -T odoo sh -c 'rm -rf "/var/lib/odoo/filestore/$1"' _ "$SCRATCH" >/dev/null 2>&1 || true; }
  rm -rf "$LOG_DIR"
}
trap cleanup EXIT

say "PLATFORM RELEASE VERIFICATION  source=$SOURCE (read only)  scratch=$SCRATCH"

# ---------------------------------------------------------------- 1) the copy
step "1) copy '$SOURCE' -> '$SCRATCH' (pg_dump: the source is only READ), neutralised in the restore transaction"
db_exists "$SOURCE" || { echo "REFUSING: no database named '$SOURCE'" >&2; exit 2; }
if db_exists "$SCRATCH"; then echo "REFUSING: '$SCRATCH' exists (a leftover?). Nothing was dropped; 'make upgrade-clean' drops it." >&2; exit 2; fi
if "${DC[@]}" exec -T odoo sh -c 'test -e "/var/lib/odoo/filestore/$1"' _ "$SCRATCH"; then
  echo "REFUSING: a filestore for '$SCRATCH' exists. Nothing was removed." >&2; exit 2
fi
SOURCE_FP_BEFORE="$(state_fp "$SOURCE")"
say "  source state fingerprint: $SOURCE_FP_BEFORE"
in_db createdb "$SCRATCH"
CREATED=1
in_db bash -c '{ pg_dump -d "$1" --no-owner || printf "%s\n" "DO \$x\$ BEGIN RAISE EXCEPTION '"'"'pg_dump of the source failed'"'"'; END \$x\$;"; cat; } \
    | psql -q -1 -v ON_ERROR_STOP=1 -d "$2" >/dev/null' _ "$SOURCE" "$SCRATCH" < scripts/upgrade/neutralize_copy.sql
"${DC[@]}" exec -T odoo sh -c 'if [ -d "/var/lib/odoo/filestore/$1" ]; then cp -a "/var/lib/odoo/filestore/$1" "/var/lib/odoo/filestore/$2"; fi' _ "$SOURCE" "$SCRATCH"
FS_CREATED=1
in_odoo odoo neutralize -d "$SCRATCH" --log-level=warn >"$LOG_DIR/neutralize.log" 2>&1 || { bad "odoo neutralize failed"; tail -5 "$LOG_DIR/neutralize.log"; exit 1; }
check "active crons on the copy" "$(q "$SCRATCH" "SELECT count(*) FROM ir_cron WHERE active")" "0"
check "the copy is a platform database (ncollection_saas installed)" \
  "$(q "$SCRATCH" "SELECT count(*) FROM ir_module_module WHERE name='ncollection_saas' AND state='installed'")" "1"

# ---------------------------------------------------------------- 2) what is behind
step "2) the copy's modules behind the code (read-only preflight)"
PRE="$(in_odoo sh -c 'python3 - "$1" "$PGHOST" "$PGUSER" "$PGPASSWORD"' _ "$SCRATCH" < scripts/upgrade/preflight_versions.py 2>&1 || true)"
printf '%s\n' "$PRE" | grep -a '^PREFLIGHT_BEHIND=' | sed 's/^/     /' || true
REQUIRED="$(marker "$PRE" PREFLIGHT_MODULES)"
check "preflight verdict" "$(marker "$PRE" PREFLIGHT)" "FAIL"
[ -n "$REQUIRED" ] || { bad "nothing is behind on '$SOURCE': there is no stale platform to prove this against"; exit 1; }
say "  required: $REQUIRED"
if [ -n "${EXPECT_MODULES:-}" ]; then check "exactly the expected modules" "$REQUIRED" "$EXPECT_MODULES"; fi
versions "$SCRATCH" > "$LOG_DIR/versions_before"

# ---------------------------------------------------------------- 3) the gated server
step "3) a release-gated scratch server (no -d: no registry is preloaded; 127.0.0.1; no crons)"
"${DC[@]}" exec -d odoo sh -c 'export PGHOST="$HOST" PGUSER="$USER" PGPASSWORD="$PASSWORD"; exec odoo --http-port="$2" \
    --gevent-port="$3" --http-interface=127.0.0.1 --max-cron-threads=0 --db-filter="^$1\$" --no-database-list \
    --log-level=info >"$4" 2>&1' _ "$SCRATCH" "$PORT" "$GEVENT_PORT" "$SERVER_LOG"
SERVER=1
up=0
for _ in $(seq 1 45); do
  if "${DC[@]}" exec -T odoo python3 -c "import urllib.request; urllib.request.urlopen('$BASE/web/health', timeout=3)" >/dev/null 2>&1; then up=1; break; fi
  sleep 2
done
[ "$up" -eq 1 ] || { bad "the scratch server did not come up"; server_log | tail -20; exit 1; }
good "server up"
if server_log | grep -aq "Release gate installed: release="; then good "the gate logged its installation through Odoo's logging"; else bad "no 'Release gate installed' line in the server log"; fi

# ---------------------------------------------------------------- 4) blocked before the registry
step "4) GET /web/login on the stale platform copy"
FP_COPY="$(state_fp "$SCRATCH")"
LINE="$(probe "$SCRATCH" GET '/web/login?redirect=%2Fweb%3F')"
say "     $LINE"
check "HTTP status" "$(field "$LINE" status)" "503"
check "workspace state" "$(field "$LINE" state)" "upgrade_required"
check "the branded maintenance page" "$(field "$LINE" maintenance)" "1"
check "traceback in the body" "$(field "$LINE" traceback)" "0"
check "internal error in the body" "$(field "$LINE" internal_error)" "0"
check "the copy's state is unchanged by the request" "$(state_fp "$SCRATCH")" "$FP_COPY"
check "registries loaded by the server" "$(server_log | grep -ac 'odoo.registry: Registry loaded' || true)" "0"
GATE_LINE="$(server_log | grep -a "Release gate: db=$SCRATCH role=platform state=-->UPGRADE_REQUIRED" | head -1 || true)"
if [ -n "$GATE_LINE" ] && printf '%s' "$GATE_LINE" | grep -q "required=$REQUIRED "; then
  good "the server log names the database, its role, the state and exactly the required modules"
  say "     $(printf '%s' "$GATE_LINE" | cut -c1-260)"
else
  bad "the server log has no reasoned gate line for $SCRATCH naming required=$REQUIRED"
fi
for secret in "$ADMIN_PW" "NC_CONFIG_SYNC_KEY=" "Authorization" "PGPASSWORD"; do
  if server_log | grep -aqF -- "$secret"; then bad "the server log contains '$secret'"; fi
done

# ---------------------------------------------------------------- 5) the operator status
step "5) the operator status while the platform cannot serve its application"
STATUS="$(release status)"
check "status HTTP" "$(marker "$STATUS" RELEASE_STATUS_HTTP)" "200"
check "state" "$(marker "$STATUS" RELEASE_STATE)" "upgrade_required"
check "role" "$(marker "$STATUS" RELEASE_ROLE)" "platform"
check "required modules" "$(marker "$STATUS" RELEASE_REQUIRED)" "$REQUIRED"
FPRINT="$(marker "$STATUS" RELEASE_FINGERPRINT)"; DBPRINT="$(marker "$STATUS" RELEASE_DB_FINGERPRINT)"
if [ "${#FPRINT}" -eq 64 ] && [ "${#DBPRINT}" -eq 64 ]; then good "release fingerprint ${FPRINT:0:12}, database fingerprint ${DBPRINT:0:12}"; else bad "fingerprints missing"; fi
if printf '%s\n' "$STATUS" | grep -q '"checked_at": "20'; then good "checked_at present"; else bad "no checked_at"; fi
say "     last_ok=$(marker "$STATUS" RELEASE_LAST_OK)  last_error=$(marker "$STATUS" RELEASE_LAST_ERROR)"
check "status without a bearer" "$(field "$(probe "$SCRATCH" GET /ncollection/release/status)" status)" "404"
check "status with ANOTHER database's bearer" "$(field "$(probe "$SCRATCH" GET /ncollection/release/status "$SOURCE")" status)" "404"
check "the copy's state is unchanged by the status calls" "$(state_fp "$SCRATCH")" "$FP_COPY"

# ---------------------------------------------------------------- 6) the release plan
step "6) the release plan (read-only)"
PLAN="$(RELEASE_PLAN_TENANTS=0 PLATFORM_DB="$SCRATCH" bash scripts/upgrade/release_plan.sh 2>&1 || true)"
printf '%s\n' "$PLAN" | sed 's/^/     /'
check "the plan's one-run list" "$(printf '%s\n' "$PLAN" | sed -n "s/^$SCRATCH .*upgrade together, one run: //p" | tr -d '\r')" "$REQUIRED"

# ---------------------------------------------------------------- 7) the rehearsal
step "7) the rehearsal of '$SOURCE' (its own scratch copy, dropped by it)"
REH="$(bash scripts/upgrade/rehearse_upgrade.sh "$SOURCE" 2>&1 || true)"
printf '%s\n' "$REH" | grep -aE '✅|❌|REHEARSAL' | sed 's/^/     /' | tail -25
check "rehearsal verdict" "$(marker "$REH" REHEARSAL)" "PASS"
check "the rehearsal's module list" "$(marker "$REH" '  REHEARSAL_UPGRADE_MODULES' | cut -d' ' -f1)" "$REQUIRED"

# ---------------------------------------------------------------- 8) hold + exact upgrade + validation
step "8) hold the copy, upgrade EXACTLY $REQUIRED, validate the registry and the schema"
HOLD="$(release hold upgrading platform release)"
check "hold" "$(marker "$HOLD" RELEASE_STATE)" "upgrading"
case "$REQUIRED" in *[!a-z0-9_,]*|all|*,all|all,*|*,all,*) bad "refusing the module list '$REQUIRED'"; exit 1 ;; esac
if in_odoo odoo -d "$SCRATCH" -u "$REQUIRED" --no-http --stop-after-init --max-cron-threads=0 >"$LOG_DIR/upgrade.log" 2>&1; then
  good "odoo -u $REQUIRED exited 0"
else
  bad "the upgrade exited non-zero"; tail -20 "$LOG_DIR/upgrade.log" | sed 's/^/     | /'
fi
neutralise
check "active crons after the upgrade (re-neutralised)" "$(q "$SCRATCH" "SELECT count(*) FROM ir_cron WHERE active")" "0"
if scripts/dev/assert_odoo_setup.sh "$LOG_DIR/upgrade.log" "-u $REQUIRED on $SCRATCH" "a dependency is missing" >/dev/null 2>&1; then
  good "setup-log guard clean (every module loaded behind the exit 0)"
else
  bad "setup-log guard refused"; scripts/dev/assert_odoo_setup.sh "$LOG_DIR/upgrade.log" "-u" "" 2>&1 | tail -5 | sed 's/^/     | /' || true
fi
if grep -qa 'Traceback (most recent call last)' "$LOG_DIR/upgrade.log"; then bad "a traceback was logged during the upgrade"; else good "no traceback during the upgrade"; fi
DRIFT="$(shell_run < scripts/upgrade/schema_drift.py | grep -a '^SCHEMA_DRIFT' || true)"
check "drift report after the upgrade (registry loaded, no module behind, no table or column missing)" "$(marker "$DRIFT" SCHEMA_DRIFT)" "0"
PRE_AFTER="$(in_odoo sh -c 'python3 - "$1" "$PGHOST" "$PGUSER" "$PGPASSWORD"' _ "$SCRATCH" < scripts/upgrade/preflight_versions.py 2>&1 || true)"
check "preflight after the upgrade" "$(marker "$PRE_AFTER" PREFLIGHT)" "PASS"

# ---------------------------------------------------------------- 9) held smoke + platform first
step "9) still held: only the operator bearer passes; Fleet Migration refuses"
STATUS="$(release status)"
check "state while held" "$(marker "$STATUS" RELEASE_STATE)" "upgrading"
check "compatible while held" "$(marker "$STATUS" RELEASE_COMPATIBLE)" "1"
check "required modules after the upgrade" "$(marker "$STATUS" RELEASE_REQUIRED)" "-"
LINE="$(probe "$SCRATCH" GET /web/login)"
check "login page without a bearer while held" "$(field "$LINE" status)/$(field "$LINE" state)" "503/upgrading"
LINE="$(probe "$SCRATCH" GET /web/login "$SCRATCH")"
check "login page with the operator bearer (held smoke)" "$(field "$LINE" status)/$(field "$LINE" traceback)" "200/0"
cat > "$LOG_DIR/fleet.py" <<PY
from odoo.exceptions import UserError
env.cr.execute("UPDATE res_users SET password = %s WHERE id = 2", (env["res.users"]._crypt_context().hash("$ADMIN_PW"),))
env.cr.execute("SELECT login FROM res_users WHERE id = 2")
print("ADMIN_LOGIN=%s" % env.cr.fetchone()[0])
env.cr.commit()
try:
    env["ncollection.fleet.migration"]._nc_assert_platform_ready()
    print("PLATFORM_FIRST=ALLOWED")
except UserError as exc:
    print("PLATFORM_FIRST=REFUSED")
    print("PLATFORM_FIRST_MESSAGE=%s" % str(exc).replace("\n", " ")[:300])
PY
FLEET="$(shell_run < "$LOG_DIR/fleet.py")"
ADMIN_LOGIN="$(marker "$FLEET" ADMIN_LOGIN)"
check "Fleet Migration while the platform is held" "$(marker "$FLEET" PLATFORM_FIRST)" "REFUSED"
say "     $(marker "$FLEET" PLATFORM_FIRST_MESSAGE)"

# ---------------------------------------------------------------- 10) open -> READY -> normal traffic
step "10) open: READY, then normal traffic"
OPEN="$(release open)"
check "open" "$(marker "$OPEN" RELEASE_STATUS_HTTP)/$(marker "$OPEN" RELEASE_STATE)" "200/ready"
LAST_OK="$(marker "$OPEN" RELEASE_LAST_OK)"
case "$LAST_OK" in 20*" release=$FPRINT") good "last successful release recorded ($LAST_OK)" ;; *) bad "last_ok is '$LAST_OK'" ;; esac
LINE="$(probe "$SCRATCH" GET /web/login)"
check "login page, no bearer" "$(field "$LINE" status)/$(field "$LINE" traceback)/$(field "$LINE" maintenance)" "200/0/0"
SMOKE="$("${DC[@]}" exec -T odoo python3 - "$BASE" "$ADMIN_LOGIN" "$ADMIN_PW" < scripts/upgrade/http_smoke.py 2>&1 || true)"
printf '%s\n' "$SMOKE" | grep -a '^HTTP_SMOKE ' | sed 's/^/     /' || true
check "HTTP smoke (health, login page, POST login, web client)" "$(marker "$SMOKE" HTTP_SMOKE)" "PASS"
FLEET="$(shell_run < "$LOG_DIR/fleet.py")"
check "Fleet Migration once the platform is READY" "$(marker "$FLEET" PLATFORM_FIRST)" "ALLOWED"
if server_log | grep -aq "Release gate: db=$SCRATCH role=platform state=UPGRADING->READY serving normal traffic"; then
  good "the server log records the transition to READY"
else
  bad "no UPGRADING->READY line for $SCRATCH in the server log"
fi
check "tracebacks in the scratch server log" "$(server_log | grep -ac 'Traceback (most recent call last)' || true)" "0"
versions "$SCRATCH" > "$LOG_DIR/versions_after"
# comm, not diff: diff exits 1 on exactly the difference this step expects, and set -e + pipefail make that an exit
CHANGED="$(LC_ALL=C comm -13 <(LC_ALL=C sort "$LOG_DIR/versions_before") <(LC_ALL=C sort "$LOG_DIR/versions_after") \
  | sed -n 's/^\([^=]*\)=.*/\1/p' | sort | paste -sd, -)"
check "modules whose version changed (exactly the required set, nothing else)" "$CHANGED" "$REQUIRED"

# ---------------------------------------------------------------- 11) the source
step "11) the source database"
check "source state fingerprint unchanged" "$(state_fp "$SOURCE")" "$SOURCE_FP_BEFORE"

say ""
if [ "$fail" -eq 0 ]; then say "PLATFORM_RELEASE=PASS"; exit 0; fi
say "PLATFORM_RELEASE=FAIL"
exit 1
