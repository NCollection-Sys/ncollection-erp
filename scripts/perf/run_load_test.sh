#!/usr/bin/env bash
# ============================================================================
#  P3-T03 — Odoo load-test runner (k6)
# ============================================================================
#  Drives scripts/perf/load_test.js against the PRODUCTION routing model
#  (db_filter=^%d$, Host-based tenant selection) using the routing overlay, then
#  restores the base dev stack. k6 runs as the official grafana/k6 container on
#  the compose network — no host install.
#
#  Parameterised for both the dev-box baseline and the full staging run:
#    TARGETS  JSON [{sub,db,login,password}, ...]   (default: ncollection)
#    STAGES   k6 ramping-vus stages JSON            (default: ramp to 10 VUs)
#    BASE_URL edge base on the compose net          (default: http://nginx)
#  e.g. staging:  STAGES='[{"duration":"1m","target":50},{"duration":"3m","target":50},{"duration":"30s","target":0}]' \
#                 TARGETS='[{"sub":"a.ncollectionerp.com","db":"a",...}, ...x3]' ./scripts/perf/run_load_test.sh
#  SECURITY: for the REAL staging run do NOT pass live tenant creds inline (they
#  land in shell history / `ps` / `docker inspect`) — source TARGETS from a 0600
#  file and rotate the admin password afterwards. The dev fixture path is admin/admin.
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")/../.."

# Network is DERIVED from the odoo container below (Rule 11 / R-006 — never
# hardcode the project-derived name; it breaks under COMPOSE_PROJECT_NAME).
NET="${LOAD_TEST_NETWORK:-}"
BASE_URL="${BASE_URL:-http://nginx}"
DB_USER="${DB_USER:-odoo}"
DB_PASSWORD="${DB_PASSWORD:-odoo}"
# Dedicated load-test fixtures (own namespace — NEVER rt*/e2e*/prov*, per the
# fixture-ownership rule). Created with a known admin/admin, reached by Host
# under db_filter. Drop them with: make load-test-clean
FIXTURES="${FIXTURES:-loadtesta loadtestb loadtestc}"
ADMIN_LOGIN="admin"
ADMIN_PW="admin"                 # dev-only test credential (never prod)
DEFAULT_STAGES='[{"duration":"15s","target":10},{"duration":"30s","target":10},{"duration":"10s","target":0}]'
STAGES="${STAGES:-$DEFAULT_STAGES}"
OUTDIR="$(pwd)/scripts/perf/results"
K6_IMAGE="grafana/k6:latest"
ROUTING=(docker compose -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.routing.yml)

restore() {
    echo "== restore base dev stack =="
    # Match `make up` EXACTLY (base + dev overlay) — a bare `docker compose up`
    # would drop the dev overlay (nginx/pgadmin/--dev flags) and leave a
    # half-restored stack.
    docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d >/dev/null 2>&1 || \
        echo "WARN: could not auto-restore base stack; run 'make up' manually" >&2
}
trap restore EXIT

mkdir -p "$OUTDIR"

echo "== routing stack up (db_filter=^%d\$, Host routing) =="
"${ROUTING[@]}" up -d

odoo_id=$("${ROUTING[@]}" ps -q odoo)
st=""
for _ in $(seq 1 40); do
    st=$(docker inspect --format '{{.State.Health.Status}}' "$odoo_id" 2>/dev/null || echo "")
    [ "$st" = "healthy" ] && break
    sleep 2
done
echo "odoo health: ${st:-unknown}"
if [ "$st" != "healthy" ]; then
    echo "FATAL: odoo did not become healthy" >&2
    exit 1
fi

# Derive the compose network from the odoo container (never hardcode the
# project-derived name — Rule 11 / R-006). k6 attaches to it to reach `nginx`.
if [ -z "$NET" ]; then
    NET=$(docker inspect "$odoo_id" \
        --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{"\n"}}{{end}}' \
        | head -1)
fi
echo "network: ${NET:-UNKNOWN}"
if [ -z "$NET" ]; then
    echo "FATAL: could not derive the compose network" >&2
    exit 1
fi

dbid="$("${ROUTING[@]}" ps -q db)"

db_exists() {  # db_exists <name>
    docker exec "$dbid" psql -U "$DB_USER" -d postgres -tAc \
        "SELECT 1 FROM pg_database WHERE datname='$1'" 2>/dev/null | grep -q 1
}

ensure_fixture() {  # ensure_fixture <db> — idempotent (create if missing)
    local db="$1"
    if db_exists "$db"; then
        echo "  fixture $db: present"
    else
        echo "  fixture $db: creating (base install)..."
        "${ROUTING[@]}" exec -T odoo odoo -d "$db" -i base --without-demo=all \
            --db_host=db --db_user="$DB_USER" --db_password="$DB_PASSWORD" \
            --stop-after-init --log-level=error >/dev/null 2>&1
    fi
    # ALWAYS (re)stamp the deterministic dev admin password — regardless of
    # whether the DB pre-existed — so a drifted password never silently breaks
    # every VU login (matches scripts/routing/verify_routing.sh).
    "${ROUTING[@]}" exec -T odoo odoo shell -d "$db" \
        --db_host=db --db_user="$DB_USER" --db_password="$DB_PASSWORD" \
        --log-level=error >/dev/null 2>&1 <<PY
env.ref('base.user_admin').write({'login': '${ADMIN_LOGIN}', 'password': '${ADMIN_PW}'})
env.cr.commit()
PY
}

# Build TARGETS from the fixtures unless the caller supplied its own (staging).
if [ -z "${TARGETS:-}" ]; then
    echo "== ensure load-test fixtures =="
    parts=""
    for db in $FIXTURES; do
        ensure_fixture "$db"
        parts="${parts:+$parts,}{\"sub\":\"${db}.localhost\",\"db\":\"${db}\",\"login\":\"${ADMIN_LOGIN}\",\"password\":\"${ADMIN_PW}\"}"
    done
    TARGETS="[${parts}]"
fi

run_k6() {  # run_k6 <summary-file>  — uses env TARGETS/STAGES/VUS/DURATION
    docker run --rm --network "$NET" \
        -e BASE_URL="$BASE_URL" -e TARGETS="$TARGETS" -e STAGES="$STAGES" \
        -e VUS="${VUS:-}" -e DURATION="${DURATION:-20s}" \
        -v "$(pwd)/scripts/perf/load_test.js:/load_test.js:ro" \
        -v "$OUTDIR:/out" \
        "$K6_IMAGE" run --summary-export="/out/$1" /load_test.js
}

extract() {  # extract <summary-file> -> "read_p95 rps err_pct"
    python3 - "$OUTDIR/$1" <<'PY'
import json, sys
m = json.load(open(sys.argv[1]))["metrics"]
read = m.get("odoo_read_ms", {})
print(f'{read.get("p(95)", 0):.1f} '
      f'{m.get("http_reqs", {}).get("rate", 0):.1f} '
      f'{m.get("http_req_failed", {}).get("value", 0)*100:.2f}')
PY
}

VU_SWEEP="${VU_SWEEP:-5 15 30 45}"
HOLD="${HOLD:-20s}"

if [ -n "$VU_SWEEP" ]; then
    CSV="$OUTDIR/load_curve.csv"
    echo "vus,read_p95_ms,rps,err_pct" > "$CSV"
    echo "== load-curve sweep: VUs = $VU_SWEEP (hold $HOLD each) =="
    for n in $VU_SWEEP; do
        echo "  -- $n VUs --"
        # k6 exits 99 when a threshold is BREACHED — which is exactly the point
        # a sweep is looking for. Capture the code (don't let pipefail/`set -e`
        # kill the sweep) so the remaining levels still run and get recorded.
        rc=0
        VUS="$n" DURATION="$HOLD" run_k6 "summary_${n}.json" \
            > "$OUTDIR/console_${n}.txt" 2>&1 || rc=$?
        grep -iE "checks_succ|odoo_read_ms\.\.|http_req_failed\.\." \
            "$OUTDIR/console_${n}.txt" | head -3 || true
        if [ "$rc" -ne 0 ]; then
            echo "     ⚠ k6 exit $rc — threshold breached at $n VUs (sweep continues)"
        fi
        read -r p95 rps err <<<"$(extract "summary_${n}.json")"
        echo "$n,$p95,$rps,$err" >> "$CSV"
        echo "     -> read_p95=${p95}ms rps=${rps} err=${err}%"
    done
    echo "== load curve =="
    cat "$CSV"
else
    echo "== single ramp run (STAGES) =="
    run_k6 "summary.json" | tee "$OUTDIR/console.txt"
fi

echo "== done — results in scripts/perf/results/ =="
