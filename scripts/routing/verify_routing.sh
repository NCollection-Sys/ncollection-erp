#!/usr/bin/env bash
# ============================================================================
#  P1-T06 — Subdomain -> Database routing & tenant-isolation proof
# ============================================================================
#  Proves, end to end and repeatably, the platform's routing backbone:
#    1. each subdomain reaches ONLY its own database (marker read + db_filter)
#    2. db_filter rejects a mismatched database on the wrong host
#    3. sessions are database-scoped and do NOT leak across tenants (both ways)
#    4. the database selector is unreachable (blocked at the Nginx edge)
#
#  Design (matches docs/ROUTING.md):
#   - Reuses the T03 dev Nginx edge; requires the routing overlay running
#     (`make routing-up`), which turns on db_filter=^%d$ + list_db=False.
#   - NO sudo and NO /etc/hosts edit: subdomains are reached with
#     `curl --resolve <sub>.localhost:80:127.0.0.1`.
#   - Idempotent: test databases are created once (skipped if present) and can
#     be dropped again with `make routing-clean`.
#
#  Exit code is non-zero if ANY check fails (CI/gate friendly).
# ============================================================================
set -euo pipefail

cd "$(dirname "$0")/../.."   # repo root

# --- configuration ----------------------------------------------------------
COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.routing.yml)
DB_USER="${DB_USER:-odoo}"
DB_PASSWORD="${DB_PASSWORD:-odoo}"
ODOO_DB_ARGS=(--db_host=db --db_user="$DB_USER" --db_password="$DB_PASSWORD")
ADMIN_LOGIN="admin"
ADMIN_PW="admin"                 # dev-only test credential (never prod)
TENANTS=(rtclienta rtclientb rtadmin)

# Per-tenant data marker (bash 3.2 on macOS has no associative arrays — use a fn).
marker_for(){
  case "$1" in
    rtclienta) echo "RTCLIENTA CO" ;;
    rtclientb) echo "RTCLIENTB CO" ;;
    rtadmin) echo "RTADMIN CO" ;;
    *)       echo "UNKNOWN CO" ;;
  esac
}

pass=0; fail=0
ok(){ echo "  ✅ PASS: $1"; pass=$((pass + 1)); }
no(){ echo "  ❌ FAIL: $1"; fail=$((fail + 1)); }
hr(){ echo "----------------------------------------------------------------------"; }

# --- database setup (idempotent) --------------------------------------------
# NOTE: `-d postgres` is REQUIRED. psql with no -d defaults the target database
# to the USERNAME ("odoo"), which does not exist here — so the query always died
# with `FATAL: database "odoo" does not exist`, db_exists() always returned
# false, and this "idempotent" setup silently re-created every test DB on every
# run. Always pass an explicit -d to psql/pg_isready (enforced by
# scripts/ci/invariants.py).
db_exists(){
  "${COMPOSE[@]}" exec -T db psql -U "$DB_USER" -d postgres -tAc \
    "SELECT 1 FROM pg_database WHERE datname='$1'" 2>/dev/null | grep -q 1
}

setup_databases(){
  echo "Setting up test databases (idempotent)…"
  for db in "${TENANTS[@]}"; do
    if db_exists "$db"; then
      echo "  • $db already exists — skip create"
    else
      echo "  • creating $db (base, no demo — this takes a minute)…"
      "${COMPOSE[@]}" exec -T odoo odoo -d "$db" -i base --without-demo=all \
        --stop-after-init "${ODOO_DB_ARGS[@]}" >/dev/null 2>&1
    fi
    # Always (re)stamp the marker + a known dev admin password — deterministic.
    "${COMPOSE[@]}" exec -T odoo odoo shell -d "$db" "${ODOO_DB_ARGS[@]}" >/dev/null 2>&1 <<PY
env.ref('base.main_company').write({'name': """$(marker_for "$db")"""})
env.ref('base.user_admin').write({'password': '${ADMIN_PW}'})
env.cr.commit()
PY
    echo "  • $db marker set -> '$(marker_for "$db")'"
  done
  hr
}

# --- HTTP helpers (reach a subdomain via the edge, no /etc/hosts) ------------
authenticate(){   # authenticate <host-sub> <payload-db> <cookiejar-file>
  local sub="$1" db="$2" jar="$3"
  curl -s --resolve "${sub}.localhost:80:127.0.0.1" -c "$jar" \
    -H 'Content-Type: application/json' \
    -d "{\"jsonrpc\":\"2.0\",\"method\":\"call\",\"params\":{\"db\":\"${db}\",\"login\":\"${ADMIN_LOGIN}\",\"password\":\"${ADMIN_PW}\"}}" \
    "http://${sub}.localhost/web/session/authenticate" || true
}

rpc_with_cookie(){   # rpc_with_cookie <host-sub> <cookiejar> <path> <json-body>
  local sub="$1" jar="$2" path="$3" body="$4"
  curl -s --resolve "${sub}.localhost:80:127.0.0.1" -b "$jar" \
    -H 'Content-Type: application/json' -d "$body" \
    "http://${sub}.localhost${path}" || true
}

http_code(){   # http_code <host-sub> <path>
  local sub="$1" path="$2"
  curl -s -o /dev/null -w '%{http_code}' \
    --resolve "${sub}.localhost:80:127.0.0.1" "http://${sub}.localhost${path}" || true
}

# JSON extractors (stdin) — tolerant of errors / missing keys.
j_uid(){ python3 -c 'import sys,json
try: r=json.load(sys.stdin).get("result") or {}
except Exception: r={}
print(r.get("uid") if isinstance(r,dict) else "")'; }
j_db(){ python3 -c 'import sys,json
try: r=json.load(sys.stdin).get("result") or {}
except Exception: r={}
print(r.get("db","") if isinstance(r,dict) else "")'; }
j_company(){ python3 -c 'import sys,json
try: r=json.load(sys.stdin).get("result") or []
except Exception: r=[]
print(r[0]["name"] if r else "")'; }

# --- the checks -------------------------------------------------------------
COMPANY_BODY='{"jsonrpc":"2.0","method":"call","params":{"model":"res.company","method":"search_read","args":[[],["name"]],"kwargs":{"limit":1}}}'
SESSION_BODY='{"jsonrpc":"2.0","method":"call","params":{}}'

check_each_subdomain_reaches_only_its_db(){
  echo "CHECK 1 — each subdomain reaches ONLY its own database"
  for db in rtclienta rtclientb; do
    local jar; jar="$(mktemp)"
    local resp; resp="$(authenticate "$db" "$db" "$jar")"
    local uid; uid="$(echo "$resp" | j_uid)"
    local sdb; sdb="$(echo "$resp" | j_db)"
    local cresp; cresp="$(rpc_with_cookie "$db" "$jar" /web/dataset/call_kw "$COMPANY_BODY")"
    local cname; cname="$(echo "$cresp" | j_company)"
    if [ -n "$uid" ] && [ "$uid" != "None" ] && [ "$sdb" = "$db" ] && [ "$cname" = "$(marker_for "$db")" ]; then
      ok "${db}.localhost -> DB '$sdb' (uid=$uid), reads own marker '$cname'"
    else
      no "${db}.localhost routing (uid='$uid' db='$sdb' marker='$cname', wanted db='$db'/'$(marker_for "$db")')"
    fi
    rm -f "$jar"
  done
  hr
}

check_db_filter_rejects_mismatch(){
  echo "CHECK 2 — db_filter rejects a mismatched database on the wrong host"
  local jar; jar="$(mktemp)"
  local resp; resp="$(authenticate rtclienta rtclientb "$jar")"   # ask rtclientb on rtclienta host
  local uid; uid="$(echo "$resp" | j_uid)"
  if [ -z "$uid" ] || [ "$uid" = "None" ]; then
    ok "rtclienta.localhost refused db=rtclientb (no session granted)"
  else
    no "rtclienta.localhost GRANTED db=rtclientb (uid=$uid) — isolation breach!"
    echo "     (preconditions were asserted at startup: db_filter IS enforcing," >&2
    echo "      so this is a real finding, not the #263 overlay-down false alarm)" >&2
  fi
  rm -f "$jar"
  hr
}

check_session_isolation(){
  echo "CHECK 3 — sessions are DB-scoped and do not leak across tenants"
  local ja jb; ja="$(mktemp)"; jb="$(mktemp)"
  authenticate rtclienta rtclienta "$ja" >/dev/null
  authenticate rtclientb rtclientb "$jb" >/dev/null
  # rtclienta cookie used on rtclientb -> must be unauthenticated, and vice-versa.
  local u_ab u_ba
  u_ab="$(rpc_with_cookie rtclientb "$ja" /web/session/get_session_info "$SESSION_BODY" | j_uid)"
  u_ba="$(rpc_with_cookie rtclienta "$jb" /web/session/get_session_info "$SESSION_BODY" | j_uid)"
  if { [ -z "$u_ab" ] || [ "$u_ab" = "None" ] || [ "$u_ab" = "False" ]; }; then
    ok "rtclienta session is NOT valid on rtclientb (uid='$u_ab')"
  else
    no "rtclienta session LEAKED to rtclientb (uid=$u_ab) — isolation breach!"
  fi
  if { [ -z "$u_ba" ] || [ "$u_ba" = "None" ] || [ "$u_ba" = "False" ]; }; then
    ok "rtclientb session is NOT valid on rtclienta (uid='$u_ba')"
  else
    no "rtclientb session LEAKED to rtclienta (uid=$u_ba) — isolation breach!"
  fi
  rm -f "$ja" "$jb"
  hr
}

check_selector_unreachable(){
  echo "CHECK 4 — the database selector/manager is unreachable (edge block)"
  for path in /web/database/manager /web/database/selector /web/database/list; do
    local code; code="$(http_code rtclienta "$path")"
    if [ "$code" = "403" ]; then
      ok "rtclienta.localhost$path -> 403"
    else
      no "rtclienta.localhost$path -> $code (expected 403)"
    fi
  done
  hr
}

# --- preconditions ----------------------------------------------------------
# CHECK 2 asks a host for ANOTHER tenant's database and calls a granted session
# an isolation breach. That reading is only valid while db_filter is actually
# enforcing. Under the permissive base/dev config there is no filter, so the
# server GRANTS correctly -- and the check reported "isolation breach!" for what
# was really "the overlay was not up" (#263).
#
# That is the worst possible false positive: it is the one check in the suite
# whose failure mode reads as cross-tenant data access, so a false alarm here
# teaches people to disbelieve the alarm that must be believed.
#
# The edge-reachability probe below is NOT sufficient -- nginx answers in both
# modes. Ask the odoo PROCESS what it is actually running. pid 1's argv is the
# ground truth: it reflects the real command including anything the entrypoint
# added, unlike the declared Config.Cmd. Exit 2 (not 1) so "did not run" stays
# distinguishable from "a check failed".
assert_routing_overlay_active(){
  local cid args
  cid="$("${COMPOSE[@]}" ps -q odoo 2>/dev/null || true)"   # derive, never hardcode (R-006)
  if [ -z "$cid" ]; then
    echo "REFUSING: no odoo container is running. Run 'make routing-up' first." >&2
    exit 2
  fi
  args="$(docker exec "$cid" ps -o args= -p 1 2>/dev/null | head -1 || true)"
  if [ -z "$args" ]; then
    echo "REFUSING: could not read the odoo process arguments, so the" >&2
    echo "  db_filter precondition cannot be confirmed. Refusing to interpret" >&2
    echo "  a granted session as an isolation breach on an unknown config." >&2
    exit 2
  fi
  case "$args" in
    *--db-filter=*) ;;
    *)
      echo "REFUSING: the routing overlay is NOT active -- odoo is running" >&2
      echo "  WITHOUT --db-filter, so nothing is enforcing subdomain->DB" >&2
      echo "  routing. CHECK 2 would report an 'isolation breach' that is" >&2
      echo "  really a missing overlay (#263)." >&2
      echo "  Fix: make routing-up" >&2
      echo "  odoo argv: $args" >&2
      exit 2 ;;
  esac
  echo "precondition OK: db_filter is enforcing (overlay active)"
}

# --- main -------------------------------------------------------------------
echo "======================================================================"
echo " P1-T06 routing & isolation proof  (edge:127.0.0.1:80, db_filter=^%d\$)"
echo "======================================================================"
if ! curl -s -o /dev/null --resolve "rtclienta.localhost:80:127.0.0.1" http://rtclienta.localhost/web/health; then
  echo "ERROR: edge not reachable on :80. Run 'make routing-up' first." >&2
  exit 2
fi

assert_routing_overlay_active
setup_databases
check_each_subdomain_reaches_only_its_db
check_db_filter_rejects_mismatch
check_session_isolation
check_selector_unreachable

echo "SUMMARY: ${pass} passed, ${fail} failed."
[ "$fail" -eq 0 ] && echo "✅ Routing is bulletproof." || echo "❌ Routing has FAILURES above."
exit "$([ "$fail" -eq 0 ] && echo 0 || echo 1)"
