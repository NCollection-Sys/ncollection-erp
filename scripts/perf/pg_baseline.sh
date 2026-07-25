#!/usr/bin/env bash
# ============================================================================
#  P3-T02 — PostgreSQL tuning baseline (before/after)
# ============================================================================
#  Measures the committed tuned config against stock postgres:16 defaults with
#  an IDENTICAL pgbench workload, so the improvement is reproducible and honest.
#
#  Fair comparison: two EPHEMERAL postgres containers, run SEQUENTIALLY (never
#  concurrently — concurrent runs would fight for host resources and skew TPS),
#  same image + same pgbench scale/clients/duration, differing ONLY in config.
#  pgbench runs INSIDE each container (no host ports, no network setup). Both
#  containers are throwaway and removed on entry and exit — it never touches the
#  dev stack, its volumes, or any real database.
#
#  Usage:   ./scripts/perf/pg_baseline.sh
#  Tunables (env): PG_BENCH_SCALE (30) · PG_BENCH_TIME (15) · PG_BENCH_CLIENTS (8)
#                  · PG_BENCH_JOBS (2)
# ============================================================================
set -euo pipefail

IMAGE="postgres:16"
SCALE="${PG_BENCH_SCALE:-30}"
DURATION="${PG_BENCH_TIME:-15}"
CLIENTS="${PG_BENCH_CLIENTS:-8}"
JOBS="${PG_BENCH_JOBS:-2}"
PW="benchpw"
CONF="$(cd "$(dirname "$0")/../.." && pwd)/config/postgres/postgresql.conf"
C_DEFAULT="ncbench_default"
C_TUNED="ncbench_tuned"

cleanup() { docker rm -f "$C_DEFAULT" "$C_TUNED" >/dev/null 2>&1 || true; }
trap cleanup EXIT
cleanup   # idempotent: clear any leftovers from a prior run

if [ ! -f "$CONF" ]; then
    echo "FATAL: tuned config not found at $CONF" >&2
    exit 1
fi

wait_ready() {
    local name="$1"
    for _ in $(seq 1 30); do
        if docker exec "$name" pg_isready -U postgres -d bench >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    echo "FATAL: $name did not become ready" >&2
    exit 1
}

# Runs pgbench in $1 and echoes: "<tpcb_tps> <select_tps>"
bench() {
    local name="$1"
    wait_ready "$name"
    # -U postgres: docker exec runs as root, and pgbench would otherwise default
    # the DB role to the OS user ("root"), which does not exist.
    docker exec "$name" pgbench -U postgres -i -s "$SCALE" -q bench >/dev/null 2>&1
    local tpcb sel
    tpcb=$(docker exec "$name" pgbench -U postgres -T "$DURATION" -c "$CLIENTS" -j "$JOBS" bench 2>/dev/null \
           | grep -oE 'tps = [0-9.]+' | head -1 | awk '{print $3}')
    sel=$(docker exec "$name" pgbench -U postgres -S -T "$DURATION" -c "$CLIENTS" -j "$JOBS" bench 2>/dev/null \
           | grep -oE 'tps = [0-9.]+' | head -1 | awk '{print $3}')
    echo "${tpcb:-0} ${sel:-0}"
}

pct() {  # pct <after> <before>
    awk -v a="$1" -v b="$2" 'BEGIN{ if (b+0==0){print "n/a"} else {printf "%+.1f%%", (a-b)/b*100} }'
}

echo "== P3-T02 pg baseline =="
echo "   image=$IMAGE scale=$SCALE duration=${DURATION}s clients=$CLIENTS"
echo "   (two throwaway containers, run sequentially)"
echo "----------------------------------------------------------------------"

echo "-- [1/2] DEFAULT postgres:16 config --"
docker run -d --name "$C_DEFAULT" -e POSTGRES_PASSWORD="$PW" -e POSTGRES_DB=bench "$IMAGE" >/dev/null
read -r DEF_TPCB DEF_SEL <<<"$(bench "$C_DEFAULT")"
docker rm -f "$C_DEFAULT" >/dev/null 2>&1 || true

echo "-- [2/2] TUNED config/postgres/postgresql.conf --"
docker run -d --name "$C_TUNED" -e POSTGRES_PASSWORD="$PW" -e POSTGRES_DB=bench \
    -v "$CONF":/etc/postgresql/postgresql.conf:ro "$IMAGE" \
    postgres -c config_file=/etc/postgresql/postgresql.conf >/dev/null
read -r TUN_TPCB TUN_SEL <<<"$(bench "$C_TUNED")"
docker rm -f "$C_TUNED" >/dev/null 2>&1 || true

echo "----------------------------------------------------------------------"
printf "%-22s %12s %12s %10s\n" "workload" "default" "tuned" "delta"
printf "%-22s %12s %12s %10s\n" "TPC-B (read/write)" "$DEF_TPCB" "$TUN_TPCB" "$(pct "$TUN_TPCB" "$DEF_TPCB")"
printf "%-22s %12s %12s %10s\n" "SELECT-only (-S)" "$DEF_SEL" "$TUN_SEL" "$(pct "$TUN_SEL" "$DEF_SEL")"
echo "----------------------------------------------------------------------"
echo "tps = transactions/sec (higher is better). Absolute numbers are host-"
echo "specific; the DELTA is the tuning signal. Record it in"
echo "docs/perf/P3-T02_pg_tuning_baseline.md."
