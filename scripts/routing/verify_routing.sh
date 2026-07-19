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
TENANTS=(clienta clientb admin)

# Per-tenant data marker (bash 3.2 on macOS has no associative arrays — use a fn).
marker_for(){
  case "$1" in
    clienta) echo "CLIENTA CO" ;;
    clientb) echo "CLIENTB CO" ;;
    admin)   echo "ADMIN CO" ;;
    *)       echo "UNKNOWN CO" ;;
  esac
}

pass=0; fail=0
ok(){ echo "  ✅ PASS: $1"; pass=$((pass + 1)); }
no(){ echo "  ❌ FAIL: $1"; fail=$((fail + 1)); }
hr(){ echo "----------------------------------------------------------------------"; }

# --- database setup (idempotent) --------------------------------------------
db_exists(){
  "${COMPOSE[@]}" exec -T db psql -U "$DB_USER" -tAc \
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
  for db in clienta clientb; do
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
  local resp; resp="$(authenticate clienta clientb "$jar")"   # ask clientb on clienta host
  local uid; uid="$(echo "$resp" | j_uid)"
  if [ -z "$uid" ] || [ "$uid" = "None" ]; then
    ok "clienta.localhost refused db=clientb (no session granted)"
  else
    no "clienta.localhost GRANTED db=clientb (uid=$uid) — isolation breach!"
  fi
  rm -f "$jar"
  hr
}

check_session_isolation(){
  echo "CHECK 3 — sessions are DB-scoped and do not leak across tenants"
  local ja jb; ja="$(mktemp)"; jb="$(mktemp)"
  authenticate clienta clienta "$ja" >/dev/null
  authenticate clientb clientb "$jb" >/dev/null
  # clienta cookie used on clientb -> must be unauthenticated, and vice-versa.
  local u_ab u_ba
  u_ab="$(rpc_with_cookie clientb "$ja" /web/session/get_session_info "$SESSION_BODY" | j_uid)"
  u_ba="$(rpc_with_cookie clienta "$jb" /web/session/get_session_info "$SESSION_BODY" | j_uid)"
  if { [ -z "$u_ab" ] || [ "$u_ab" = "None" ] || [ "$u_ab" = "False" ]; }; then
    ok "clienta session is NOT valid on clientb (uid='$u_ab')"
  else
    no "clienta session LEAKED to clientb (uid=$u_ab) — isolation breach!"
  fi
  if { [ -z "$u_ba" ] || [ "$u_ba" = "None" ] || [ "$u_ba" = "False" ]; }; then
    ok "clientb session is NOT valid on clienta (uid='$u_ba')"
  else
    no "clientb session LEAKED to clienta (uid=$u_ba) — isolation breach!"
  fi
  rm -f "$ja" "$jb"
  hr
}

check_selector_unreachable(){
  echo "CHECK 4 — the database selector/manager is unreachable (edge block)"
  for path in /web/database/manager /web/database/selector /web/database/list; do
    local code; code="$(http_code clienta "$path")"
    if [ "$code" = "403" ]; then
      ok "clienta.localhost$path -> 403"
    else
      no "clienta.localhost$path -> $code (expected 403)"
    fi
  done
  hr
}

# --- main -------------------------------------------------------------------
echo "======================================================================"
echo " P1-T06 routing & isolation proof  (edge:127.0.0.1:80, db_filter=^%d\$)"
echo "======================================================================"
if ! curl -s -o /dev/null --resolve "clienta.localhost:80:127.0.0.1" http://clienta.localhost/web/health; then
  echo "ERROR: edge not reachable on :80. Run 'make routing-up' first." >&2
  exit 2
fi

setup_databases
check_each_subdomain_reaches_only_its_db
check_db_filter_rejects_mismatch
check_session_isolation
check_selector_unreachable

echo "SUMMARY: ${pass} passed, ${fail} failed."
[ "$fail" -eq 0 ] && echo "✅ Routing is bulletproof." || echo "❌ Routing has FAILURES above."
exit "$([ "$fail" -eq 0 ] && echo 0 || echo 1)"
