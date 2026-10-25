#!/usr/bin/env bash
# shellcheck disable=SC2015,SC2016,SC2329  # A && B || C reporting idiom; single-quoted scripts expand in the CONTAINER's shell; cleanup/drop_db run from the EXIT trap
# ============================================================================
#  UPGRADE REHEARSAL GATE - will THIS code serve THIS database, after the upgrade it needs?
# ============================================================================
#  THE FAILURE IT PREVENTS (REGRESSIONS.md R-037, and R-012b / R-017 / R-019 before it)
#  -----------------------------------------------------------------------------------
#  Odoo changes a schema only on `-u` and never compares the version in a database with the code on disk. Put newer
#  code under a database that has not been upgraded and the first request that reads a changed model answers HTTP 500
#  (`column res_users.nc_must_change_password does not exist` on GET /web/login; `column res_partner.nc_trn does not
#  exist` on POST /web/login) while /web/health keeps answering 200. Nothing in the old gates exercised that
#  sequence: verify_upgrade.sh proves migrations replay (and runs --no-http), `make doctor` only warns by hand.
#
#  THE PIPELINE (each step fails closed):
#
#     previous database state   (a COPY of a real database, or one of the upgrade suite's own fixtures)
#             |
#     [0] copy + NEUTRALISE     restore and kill switch in ONE transaction, then Odoo's own `odoo neutralize`, then
#                               PROVE 0 active crons and 0 runnable queue jobs before any Odoo process opens the copy
#     [1] drift report          which installed modules are behind the code, which stored fields have no column
#     [2] HTTP smoke BEFORE     informational: does the stale state really 500? (so the gate proves it can see it)
#     [3] targeted `-u`         ONLY the modules the preflight names (or --modules), validated: never `all`, never
#                               `base`, never a module outside the NCollection / OCA roots. Never hand-written SQL.
#     [3b] NEUTRALISE again     an upgrade can re-activate data-file crons; 0 active is proved again before [5]
#     [4] setup-log guard       assert_odoo_setup.sh (exit 0 can hide a module that did not load)
#     [5] drift + preflight     must be empty / PASS
#     [6] HTTP smoke AFTER      login page, POST login, web client, session info, brand logo, websocket: no 5xx
#             |
#     REHEARSAL=PASS | FAIL     exit 0 | 1   (exit 2: refused before anything was created)
#
#  WHAT IT NEVER DOES
#  ------------------
#  * It never touches the source database: the source is only READ (pg_dump + a copy of its filestore). All work
#    happens in a scratch database named upgrreh<source>, created by this run and dropped on every exit path (Rule 12).
#  * It never drops, overwrites or reuses a database it did not create: if upgrreh<source> (or its filestore) already
#    exists, it refuses (pre-commit review L1). One rehearsal at a time: a lock in the odoo container refuses a second.
#  * It never lets the copy do live work (review H2). A copy carries its source's crons, queue jobs and mail servers,
#    and the shared Odoo server runs the crons of every database it can list. The copy is neutralised in the same
#    transaction that creates its data, again after the upgrade, and the scratch server runs with
#    --max-cron-threads=0 on 127.0.0.1 only. RESIDUAL WINDOW, stated rather than hidden: a cron the upgrade itself
#    CREATES (new in this release, with a due next call) is active from the upgrade's commit until [3b], a few seconds;
#    re-activated existing crons are not due (their next call is pushed out and their triggers are gone).
#  * It never upgrades anything real. A PASS here means "the same targeted upgrade, run for real, is expected to leave
#    the database consistent with this code"; running that upgrade is a separate, explicit decision.
#  * It carries no credential: the database connection is the containers' own environment (odoo: HOST / USER /
#    PASSWORD, as the preflight; db: POSTGRES_USER), passed as PG* variables, never on a command line. It refuses if
#    they are missing (review M5). The scratch admin password is set on the COPY only, random per run, never printed.
#
#  USAGE
#      scripts/upgrade/rehearse_upgrade.sh <source_db>                 # copy -> rehearse -> drop the copy
#      scripts/upgrade/rehearse_upgrade.sh --in-place <fixture> [--modules a,b]
#                                       # one of the upgrade suite's fixtures, BY EXACT NAME (verify_upgrade.sh)
#  <source_db> must be a plain lowercase alphanumeric name (a tenant key is): anything else could map two sources onto
#  one scratch name.
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")/../.."   # repo root
# Git Bash on Windows must not rewrite container paths (/tmp/..., the lock and the server log) passed as arguments to
# `docker compose exec`: rewritten, the lock could never be taken and the run refused (or worse, wrote elsewhere).
export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL="*"

DC=(docker compose)
PORT=8199
GEVENT_PORT=8198
FORCE_MODULES=""
SOURCE=""
IN_PLACE=0
# --in-place may only ever name the upgrade suite's own fixtures (CLAUDE.md fixture table), never a prefix match: a real
# tenant called e.g. `upgradelogistics` must not qualify (review M7).
IN_PLACE_FIXTURES="upgrgreen upgrred upgrcore upgrdrift"
LOCK=/tmp/nc_rehearse_upgrade.lock      # inside the odoo container
RUN_ID="$(date +%Y%m%d%H%M%S)_$$"

while [ $# -gt 0 ]; do
  case "$1" in
    --in-place) IN_PLACE=1; SOURCE="${2:?--in-place needs a database}"; shift 2 ;;
    --modules)  FORCE_MODULES="${2:?--modules needs a comma separated list}"; shift 2 ;;
    -h|--help)  sed -n '2,55p' "$0"; exit 0 ;;
    -*)         echo "unknown option: $1" >&2; exit 2 ;;
    *)          SOURCE="$1"; shift ;;
  esac
done
[ -n "$SOURCE" ] || { echo "usage: $0 <source_db> | --in-place <fixture> [--modules a,b]" >&2; exit 2; }
case "$SOURCE" in
  *[!a-z0-9]*) echo "REFUSING: '$SOURCE' is not a plain lowercase alphanumeric database name" >&2; exit 2 ;;
esac

if [ "$IN_PLACE" -eq 1 ]; then
  allowed=0
  for fixture in $IN_PLACE_FIXTURES; do [ "$SOURCE" = "$fixture" ] && allowed=1; done
  [ "$allowed" -eq 1 ] || {
    echo "REFUSING: --in-place only works on the upgrade suite's own fixtures ($IN_PLACE_FIXTURES), got '$SOURCE'" >&2
    exit 2; }
  SCRATCH="$SOURCE"
else
  case "$SOURCE" in upgrreh*) echo "REFUSING: '$SOURCE' is a rehearsal scratch copy, never a source" >&2; exit 2 ;; esac
  SCRATCH="upgrreh$SOURCE"
  [ "${#SCRATCH}" -le 63 ] || { echo "REFUSING: the scratch name '$SCRATCH' is longer than PostgreSQL allows" >&2; exit 2; }
fi
if [ -n "$FORCE_MODULES" ]; then
  case "$FORCE_MODULES" in *[!a-z0-9_,]*|all|*,all|all,*|*,all,*) echo "REFUSING: --modules must be plain module names, never 'all'" >&2; exit 2 ;; esac
fi

# ---------------------------------------------------------------- the containers' own credentials (never a literal)
# odoo container: HOST / USER / PASSWORD (what the preflight uses) become PGHOST / PGUSER / PGPASSWORD, which Odoo,
# psycopg2 and the odoo CLI read; db container: POSTGRES_USER over the local socket.
in_odoo(){
  "${DC[@]}" exec -T odoo sh -c '[ -n "${HOST:-}" ] && [ -n "${USER:-}" ] && [ -n "${PASSWORD:-}" ] \
    || { echo "REHEARSAL_NO_DB_CREDENTIALS: the odoo container has no HOST / USER / PASSWORD" >&2; exit 97; }
    export PGHOST="$HOST" PGUSER="$USER" PGPASSWORD="$PASSWORD"; exec "$@"' _ "$@"
}
in_odoo_detached(){
  "${DC[@]}" exec -d odoo sh -c 'export PGHOST="$HOST" PGUSER="$USER" PGPASSWORD="$PASSWORD"; exec "$@"' _ "$@"
}
in_db(){
  "${DC[@]}" exec -T db sh -c '[ -n "${POSTGRES_USER:-}" ] \
    || { echo "REHEARSAL_NO_DB_CREDENTIALS: the db container has no POSTGRES_USER" >&2; exit 97; }
    export PGUSER="$POSTGRES_USER"; exec "$@"' _ "$@"
}
"${DC[@]}" exec -T odoo sh -c '[ -n "${HOST:-}" ] && [ -n "${USER:-}" ] && [ -n "${PASSWORD:-}" ]' \
  || { echo "REFUSING: the odoo container has no database credentials (HOST / USER / PASSWORD)" >&2; exit 2; }
"${DC[@]}" exec -T db sh -c '[ -n "${POSTGRES_USER:-}" ]' \
  || { echo "REFUSING: the db container has no POSTGRES_USER" >&2; exit 2; }

LOG_DIR="$(mktemp -d)"
ADMIN_PW="$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"
SERVER_LOG="/tmp/rehearse_server_${SCRATCH}_${RUN_ID}.log"     # per run: an earlier run's log is never overwritten
fail=0
LOCKED=0
CREATED=0
FS_CREATED=0
say(){ printf '%s\n' "$*"; }
step(){ say ""; say "== $* =="; }
bad(){ say "  ❌ $*"; fail=1; }
good(){ say "  ✅ $*"; }
verdict_fail(){ say ""; say "REHEARSAL=FAIL"; exit 1; }

db_exists(){ [ "$(in_db psql -d postgres -Atc "SELECT count(*) FROM pg_database WHERE datname='$1'" | tr -d '[:space:]')" = "1" ]; }
drop_db(){
  in_db psql -d postgres -c \
    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$1' AND pid<>pg_backend_pid()" >/dev/null 2>&1 || true
  in_db dropdb --if-exists "$1" >/dev/null 2>&1 || true
}
stop_server(){
  "${DC[@]}" exec -T odoo sh -c "pkill -f 'http-port=$PORT' 2>/dev/null; sleep 1; pkill -9 -f 'http-port=$PORT' 2>/dev/null; true" >/dev/null 2>&1 || true
}
cleanup(){
  [ "$LOCKED" -eq 1 ] && stop_server
  # Only what THIS run created is removed: a database or filestore that existed before is never touched.
  [ "$CREATED" -eq 1 ] && drop_db "$SCRATCH"
  [ "$FS_CREATED" -eq 1 ] && { "${DC[@]}" exec -T odoo sh -c 'rm -rf "/var/lib/odoo/filestore/$1"' _ "$SCRATCH" >/dev/null 2>&1 || true; }
  [ "$LOCKED" -eq 1 ] && { "${DC[@]}" exec -T odoo sh -c 'rm -rf "$1"' _ "$LOCK" >/dev/null 2>&1 || true; }
  rm -rf "$LOG_DIR"
}
trap cleanup EXIT

# ---------------------------------------------------------------- neutralisation (review H2)
neutralise_sql(){       # the kill switch, in one transaction, on the scratch database
  in_db psql -q -1 -v ON_ERROR_STOP=1 -d "$SCRATCH" < scripts/upgrade/neutralize_copy.sql >/dev/null
}
odoo_neutralise(){      # Odoo's own: every installed module's data/neutralize.sql (SQL only, no registry is loaded)
  in_odoo odoo neutralize -d "$SCRATCH" --log-level=warn >"$LOG_DIR/neutralize.log" 2>&1
}
live_work(){            # prints "<active crons> <runnable queue jobs>" on the scratch database
  local crons jobs=0
  crons="$(in_db psql -d "$SCRATCH" -Atc "SELECT count(*) FROM ir_cron WHERE active" | tr -d '[:space:]')"
  if [ "$(in_db psql -d "$SCRATCH" -Atc "SELECT to_regclass('public.queue_job') IS NOT NULL" | tr -d '[:space:]')" = "t" ]; then
    jobs="$(in_db psql -d "$SCRATCH" -Atc "SELECT count(*) FROM queue_job WHERE state IN ('wait_dependencies','pending','enqueued','started')" | tr -d '[:space:]')"
  fi
  printf '%s %s\n' "${crons:-unreadable}" "${jobs:-unreadable}"
}
assert_neutralised(){  # $1 = when ; fail closed: nothing may run while live work exists on the copy
  local crons jobs
  read -r crons jobs <<<"$(live_work)"
  say "  REHEARSAL_ACTIVE_CRONS=$crons ($1)"
  say "  REHEARSAL_RUNNABLE_JOBS=$jobs ($1)"
  if [ "$crons" = "0" ] && [ "$jobs" = "0" ]; then
    good "neutralised ($1): 0 active crons, 0 runnable queue jobs"
  else
    bad "the scratch copy still has live work ($1): $crons active cron(s), $jobs runnable job(s). Stopping before any Odoo process serves it."
    verdict_fail
  fi
}

validate_modules(){     # $1 = comma separated list ; the ONE rule, shared with the preflight (review M2)
  local out
  out="$("${DC[@]}" exec -T odoo python3 - --validate "$1" < scripts/upgrade/preflight_versions.py 2>&1 || true)"
  printf '%s\n' "$out" | sed -n 's/^MODULES_REFUSED=/     refused: /p'
  [ "$(printf '%s\n' "$out" | sed -n 's/^MODULES=//p' | head -1 | tr -d '\r')" = "OK" ]
}

shell_marker(){   # run a script inside `odoo shell` on the scratch DB, print its stdout
  in_odoo odoo shell -d "$SCRATCH" --no-http --log-level=error 2>&1
}
drift(){          # prints the SCHEMA_DRIFT_* markers for the scratch DB
  shell_marker < scripts/upgrade/schema_drift.py | grep -a '^SCHEMA_DRIFT' || true
}
preflight(){      # prints the PREFLIGHT_* markers for the scratch DB (read-only; the universe --validate uses)
  in_odoo sh -c 'python3 - "$1" "$PGHOST" "$PGUSER" "$PGPASSWORD"' _ "$SCRATCH" < scripts/upgrade/preflight_versions.py 2>&1 \
    | grep -a '^PREFLIGHT' || true
}
marker(){ printf '%s\n' "$1" | sed -n "s/^$2=//p" | head -1 | tr -d '\r'; }

http_smoke(){     # $1 = label ; sets SMOKE_RESULT=PASS|FAIL ; prints the steps
  local label="$1" out
  # --max-cron-threads=0: the scratch server never runs a cron. --http-interface=127.0.0.1: nothing outside the
  # container (another compose service, the host) can reach the copy while it is up.
  in_odoo_detached sh -c 'exec odoo -d "$1" --http-port="$2" --gevent-port="$3" --http-interface=127.0.0.1 \
      --max-cron-threads=0 --db-filter="^$1\$" --no-database-list --log-level=warn >"$4" 2>&1' \
    _ "$SCRATCH" "$PORT" "$GEVENT_PORT" "$SERVER_LOG"
  local up=0
  for _ in $(seq 1 45); do
    if "${DC[@]}" exec -T odoo python3 -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:$PORT/web/health', timeout=3)" >/dev/null 2>&1; then up=1; break; fi
    sleep 2
  done
  if [ "$up" -ne 1 ]; then
    say "  (the scratch server did not come up in 90s: registry load failed)"
    "${DC[@]}" exec -T odoo sh -c 'tail -25 "$1"' _ "$SERVER_LOG" 2>&1 | sed 's/^/     | /'
    SMOKE_RESULT=FAIL; stop_server; return 0
  fi
  local entry_args=()
  # A TENANT (it holds the platform's config-sync service account) also gets the Company Entry probe: the grant, the
  # redeem, Home, exit and replay. The bearer derives from the tenant's REAL name: a scratch copy keeps its source's key.
  if [ -n "${ENTRY_KEY_DB:-}" ]; then entry_args=("$ENTRY_KEY_DB" "$SCRATCH"); fi
  out="$("${DC[@]}" exec -T odoo python3 - "http://127.0.0.1:$PORT" "$ADMIN_LOGIN" "$ADMIN_PW" "${entry_args[@]}" < scripts/upgrade/http_smoke.py 2>&1 || true)"
  { printf '%s\n' "$out" | grep -a '^HTTP_SMOKE ' | sed "s/^HTTP_SMOKE /     [$label] /"; } || true   # display only
  SMOKE_RESULT="$(marker "$out" 'HTTP_SMOKE')"; [ -n "$SMOKE_RESULT" ] || SMOKE_RESULT=FAIL
  SERVER_ERRORS="$("${DC[@]}" exec -T odoo sh -c 'grep -ac "Traceback (most recent call last)" "$1" || true' _ "$SERVER_LOG" | tr -d '[:space:]')"
  # Never hide the server's own words: the distinct exception lines it logged while serving the smoke (display only).
  { "${DC[@]}" exec -T odoo sh -c 'grep -aE "^(psycopg2\.|odoo\.exceptions\.|[A-Za-z_.]+Error)" "$1" | sort | uniq -c | sort -rn | head -4' _ "$SERVER_LOG" 2>/dev/null | sed "s/^/     [$label] server log: /"; } || true
  stop_server
}

say "UPGRADE REHEARSAL  source=$SOURCE  scratch=$SCRATCH  (modules: $( [ -n "$FORCE_MODULES" ] && echo "$FORCE_MODULES" || echo "those the preflight names" ))"

# ---------------------------------------------------------------- one rehearsal at a time (review L1)
if "${DC[@]}" exec -T odoo sh -c 'mkdir "$1" 2>/dev/null && printf "%s\n" "$2" > "$1/owner"' _ "$LOCK" "$SOURCE run $RUN_ID"; then
  LOCKED=1
else
  echo "REFUSING: another upgrade rehearsal holds $LOCK in the odoo container ($("${DC[@]}" exec -T odoo sh -c 'cat "$1/owner" 2>/dev/null' _ "$LOCK" | tr -d '\r')). If no rehearsal is running, inspect and remove it by hand." >&2
  exit 2
fi
if [ -n "$FORCE_MODULES" ] && ! validate_modules "$FORCE_MODULES"; then
  echo "REFUSING: --modules names a module this tool must never upgrade (see above)" >&2
  exit 2
fi

# ---------------------------------------------------------------- [0] prepare and neutralise the scratch database
if [ "$IN_PLACE" -eq 0 ]; then
  step "0) copy '$SOURCE' -> '$SCRATCH' (the source is only READ) and neutralise it in the same transaction"
  db_exists "$SOURCE" || { echo "REFUSING: no database named '$SOURCE'" >&2; exit 2; }
  if db_exists "$SCRATCH"; then
    echo "REFUSING: '$SCRATCH' already exists (another rehearsal, or a leftover). Nothing was dropped: inspect it, and drop it yourself only if it is a stale rehearsal copy." >&2
    exit 2
  fi
  if "${DC[@]}" exec -T odoo sh -c 'test -e "/var/lib/odoo/filestore/$1"' _ "$SCRATCH"; then
    echo "REFUSING: a filestore for '$SCRATCH' already exists. Nothing was removed: inspect it first." >&2
    exit 2
  fi
  say "  REHEARSAL_SOURCE_ACTIVE_CRONS=$(in_db psql -d "$SOURCE" -Atc "SELECT count(*) FROM ir_cron WHERE active" | tr -d '[:space:]')   (read only, for the record)"
  in_db createdb "$SCRATCH"
  CREATED=1
  # ONE transaction (-1): the data and the kill switch commit together, so no other session (the shared server's cron
  # runner included) ever sees the copy with an active cron. If pg_dump fails half way, the RAISE aborts the whole
  # restore instead of committing a partial copy.
  in_db bash -c '{ pg_dump -d "$1" --no-owner || printf "%s\n" "DO \$x\$ BEGIN RAISE EXCEPTION '"'"'pg_dump of the source failed: the copy is incomplete'"'"'; END \$x\$;"; cat; } \
      | psql -q -1 -v ON_ERROR_STOP=1 -d "$2" >/dev/null' _ "$SOURCE" "$SCRATCH" < scripts/upgrade/neutralize_copy.sql
  read -r restored_crons _jobs <<<"$(live_work)"
  say "  REHEARSAL_ACTIVE_CRONS_AT_RESTORE=$restored_crons"
  good "restored and neutralised in one transaction"
  "${DC[@]}" exec -T odoo sh -c 'if [ -d "/var/lib/odoo/filestore/$1" ]; then cp -a "/var/lib/odoo/filestore/$1" "/var/lib/odoo/filestore/$2"; fi' _ "$SOURCE" "$SCRATCH"
  FS_CREATED=1
else
  step "0) neutralise the fixture '$SCRATCH' before anything serves it"
  db_exists "$SCRATCH" || { echo "REFUSING: no database named '$SCRATCH'" >&2; exit 2; }
  read -r before_crons _jobs <<<"$(live_work)"
  say "  REHEARSAL_CRONS_BEFORE_NEUTRALISE=$before_crons"
  neutralise_sql
  good "kill switch applied"
fi
if odoo_neutralise; then good "odoo neutralize applied (each installed module's neutralize.sql)"; else
  bad "odoo neutralize failed"; tail -5 "$LOG_DIR/neutralize.log" | sed 's/^/     | /'; verdict_fail; fi
assert_neutralised "before any Odoo process serves the copy"
good "scratch database ready"

# A known admin on the COPY (random password, never printed). Raw SQL on purpose: on a stale schema the ORM cannot
# read res.users at all - that is the failure being rehearsed.
step "0b) a known administrator on the scratch copy"
PWSCRIPT="$LOG_DIR/setpw.py"
cat > "$PWSCRIPT" <<PY
env.cr.execute("SELECT login FROM res_users WHERE id = 2")
row = env.cr.fetchone()
print("ADMIN_LOGIN=%s" % (row[0] if row else ""))
env.cr.execute("UPDATE res_users SET password = %s WHERE id = 2", (env["res.users"]._crypt_context().hash("$ADMIN_PW"),))
env.cr.commit()
PY
ADMIN_LOGIN="$(shell_marker < "$PWSCRIPT" | sed -n 's/^ADMIN_LOGIN=//p' | head -1 | tr -d '\r')"
[ -n "$ADMIN_LOGIN" ] || { bad "the scratch database has no administrator (res_users.id = 2)"; exit 1; }
good "administrator '$ADMIN_LOGIN' (password set on the copy only)"

# Is this database a TENANT (does it hold the platform's config-sync service account and its key)? Then Company Entry is
# part of what must keep working, and the smoke runs it. A platform database has no such key and is not probed.
ENTRY_KEY_DB="${REHEARSE_ENTRY_KEY_DB:-}"
if [ -z "$ENTRY_KEY_DB" ] && [ "$IN_PLACE" -eq 0 ]; then
  HAS_KEY="$(in_db psql -d "$SCRATCH" -Atc "SELECT count(*) FROM res_users_apikeys k JOIN res_users u ON u.id = k.user_id WHERE u.login = 'config-sync@ncollection.internal'" 2>/dev/null | tr -d '[:space:]' || true)"
  [ "${HAS_KEY:-0}" != "0" ] && ENTRY_KEY_DB="$SOURCE"
fi
[ -n "$ENTRY_KEY_DB" ] && good "tenant database: the Company Entry probe is part of the smoke (bearer derived for '$ENTRY_KEY_DB')" || say "  (not a tenant database: no Company Entry probe)"

# ---------------------------------------------------------------- [1] [2] before
step "1) drift report and preflight BEFORE the upgrade"
BEFORE="$(drift)"
{ printf '%s\n' "$BEFORE" | grep -a '^SCHEMA_DRIFT_ITEM=' | sed 's/^SCHEMA_DRIFT_ITEM=/     /' | head -20; } || true   # display only: no items is the GOOD case
DRIFT_ALL="$(marker "$BEFORE" SCHEMA_DRIFT_MODULES)"
B_COLS="$(marker "$BEFORE" SCHEMA_DRIFT_COLUMNS)"; B_TABS="$(marker "$BEFORE" SCHEMA_DRIFT_TABLES)"
PRE_BEFORE="$(preflight)"
{ printf '%s\n' "$PRE_BEFORE" | grep -aE '^PREFLIGHT_(BEHIND|MISSING|ERROR)' | sed 's/^/     /'; } || true   # display only
PRE_VERDICT="$(marker "$PRE_BEFORE" PREFLIGHT)"
BEHIND="$(marker "$PRE_BEFORE" PREFLIGHT_MODULES)"
say "  behind (preflight): ${BEHIND:-none}   missing columns: ${B_COLS:-?}   missing tables: ${B_TABS:-?}"
case "$PRE_VERDICT" in
  PASS|FAIL) ;;
  *) bad "the preflight could not check the copy (PREFLIGHT=${PRE_VERDICT:-unreadable}): nothing is upgraded"; verdict_fail ;;
esac
# The drift report sees EVERY installed module; the preflight only the NCollection / OCA ones. A module behind outside
# that universe (an Odoo core module after an image change) is never upgraded here: refuse, do not narrow silently.
OUTSIDE=""
for module in ${DRIFT_ALL//,/ }; do
  case ",$BEHIND," in *",$module,"*) ;; *) OUTSIDE="${OUTSIDE:+$OUTSIDE,}$module" ;; esac
done
if [ -n "$OUTSIDE" ]; then
  bad "behind the code OUTSIDE the NCollection / OCA modules: $OUTSIDE. Upgrading Odoo itself is a separate, explicit decision; this tool refuses."
  verdict_fail
fi

step "2) HTTP smoke BEFORE (informational: shows whether the stale state really answers 500)"
http_smoke before
say "  before-upgrade smoke: $SMOKE_RESULT"
BEFORE_SMOKE="$SMOKE_RESULT"

# ---------------------------------------------------------------- [3] targeted upgrade
step "3) targeted upgrade (never -u all, never base, only NCollection / OCA modules)"
MODS="${FORCE_MODULES:-$BEHIND}"
if [ -z "$MODS" ]; then
  if [ "${B_COLS:-0}" != "0" ] || [ "${B_TABS:-0}" != "0" ]; then
    bad "columns/tables are missing but NO module is behind: an upgrade cannot be chosen for this. Investigate; nothing was changed."
  else
    good "no module is behind the code: nothing to upgrade"
  fi
else
  if ! validate_modules "$MODS"; then
    bad "the module list '$MODS' contains a module this tool must never upgrade: nothing was upgraded"
    verdict_fail
  fi
  say "  upgrading ONLY: $MODS"
  UPLOG="$LOG_DIR/upgrade.log"
  # Held while it upgrades: the moment its versions match the code the copy is READY, and data files can re-activate
  # crons before [3b] runs; a shared cron runner (no -d) would run them. The release gate runs no cron of a held
  # database. Lifted right after the copy is neutralised again.
  in_db psql -q -v ON_ERROR_STOP=1 -d "$SCRATCH" -c "INSERT INTO ir_config_parameter (key, value) VALUES ('ncollection.release.hold', 'upgrading:rehearsal copy') ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value" >/dev/null
  if in_odoo odoo -d "$SCRATCH" -u "$MODS" --no-http --stop-after-init --max-cron-threads=0 >"$UPLOG" 2>&1; then
    good "odoo exited 0"
  else
    bad "odoo exited non-zero"; tail -20 "$UPLOG" | sed 's/^/     | /'
  fi
  # [3b] immediately: an upgrade re-loads data files, which can re-activate crons on the copy.
  neutralise_sql
  assert_neutralised "after the upgrade"
  in_db psql -q -v ON_ERROR_STOP=1 -d "$SCRATCH" -c "DELETE FROM ir_config_parameter WHERE key = 'ncollection.release.hold'" >/dev/null
  if "$(pwd)/scripts/dev/assert_odoo_setup.sh" "$UPLOG" "rehearsal -u $MODS on $SCRATCH" "a dependency of an upgraded module is missing" >/dev/null 2>&1; then
    good "setup-log guard clean (no module skipped behind an exit 0)"
  else
    bad "setup-log guard refused (a module did not load behind an exit 0)"; "$(pwd)/scripts/dev/assert_odoo_setup.sh" "$UPLOG" "rehearsal" "" 2>&1 | tail -6 | sed 's/^/     | /' || true
  fi
  grep -qa 'Traceback (most recent call last)' "$UPLOG" && bad "a traceback was logged during the upgrade" || good "no traceback during the upgrade"
fi

# ---------------------------------------------------------------- [5] [6] after
step "4) drift report and preflight AFTER the upgrade"
AFTER="$(drift)"
A_FLAG="$(marker "$AFTER" SCHEMA_DRIFT)"
{ printf '%s\n' "$AFTER" | grep -a '^SCHEMA_DRIFT_ITEM=' | sed 's/^SCHEMA_DRIFT_ITEM=/     /' | head -20; } || true   # display only
if [ "$A_FLAG" = "0" ]; then good "no module behind, no missing table or column"; else bad "drift remains after the upgrade (flag='${A_FLAG:-unreadable}')"; fi
PRE_AFTER="$(marker "$(preflight)" PREFLIGHT)"
if [ "$PRE_AFTER" = "PASS" ]; then good "the preflight passes on the upgraded copy"; else bad "the preflight does not pass on the upgraded copy (PREFLIGHT=${PRE_AFTER:-unreadable})"; fi

step "5) HTTP smoke AFTER the upgrade"
SERVER_ERRORS=0
http_smoke after
if [ "$SMOKE_RESULT" = "PASS" ]; then good "every endpoint answered without a 5xx"; else bad "HTTP smoke failed"; fi
if [ "${SERVER_ERRORS:-0}" != "0" ]; then bad "the server logged ${SERVER_ERRORS} traceback(s) while serving the smoke"; else good "no traceback in the server log"; fi

# ---------------------------------------------------------------- verdict
say ""
say "----------------------------------------------------------------------"
say "  before upgrade: drift=$( [ -n "$BEHIND" ] && echo "yes ($BEHIND)" || echo none ), smoke=$BEFORE_SMOKE"
say "  after  upgrade: drift=$( [ "$A_FLAG" = "0" ] && echo none || echo YES ), smoke=$SMOKE_RESULT"
[ -n "$MODS" ] && say "  REHEARSAL_UPGRADE_MODULES=$MODS   (run exactly this list, together, for real)"
if [ "$fail" -eq 0 ]; then
  say "REHEARSAL=PASS"
  exit 0
fi
say "REHEARSAL=FAIL"
exit 1
