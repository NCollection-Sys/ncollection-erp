#!/usr/bin/env bash
# ============================================================================
#  P4-T01 — aggregation query-budget benchmark
# ============================================================================
#  Creates (or reuses) the agg* fixture database, seeds it to AGG_BENCH_ROWS
#  sale.order.line rows, and measures the aggregation engine against the
#  <500ms dashboard budget (ARCHITECTURE_DATA_PLATFORM line 267).
#
#  Fixture ownership (CLAUDE.md / R-004): this suite owns the agg* namespace
#  and drops NOTHING else. rt* / e2e* / prov* / loadtest* belong to other
#  suites. Clean up with: make agg-clean
#
#  This measures QUERY TIME vs DATA VOLUME on an otherwise-idle stack. It is
#  NOT a concurrency test — that is P3-T03 (scripts/perf/run_load_test.sh).
#  The two answer different questions; neither substitutes for the other.
#
#  Usage:
#    ./scripts/perf/run_aggregation_bench.sh              # 100k rows
#    AGG_BENCH_ROWS=10000 ./scripts/perf/run_aggregation_bench.sh
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")/../.."

DB_NAME="${AGG_BENCH_DB:-aggbench}"
ROWS="${AGG_BENCH_ROWS:-100000}"
RESULTS_DIR="scripts/perf/results"
RESULTS_FILE="${RESULTS_DIR}/aggregation.json"
COMPOSE=(docker compose)
# Rule 11 / R-006: never hardcode container names — derive them.
ODOO_RUN=("${COMPOSE[@]}" run --rm --no-deps -T odoo)
DB_ARGS=(--db_host=db --db_user=odoo --db_password=odoo)

case "$DB_NAME" in
  agg*) ;;
  *)
    echo "FATAL: AGG_BENCH_DB must start with 'agg' (got '${DB_NAME}')." >&2
    echo "       The agg* namespace is this suite's; other prefixes belong to" >&2
    echo "       other suites and dropping them destroys their fixtures." >&2
    exit 1
    ;;
esac

echo "==> P4-T01 aggregation benchmark: db=${DB_NAME} rows=${ROWS}"

db_exists() {
  "${COMPOSE[@]}" exec -T db psql -U odoo -d postgres -tAc \
    "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" | grep -q 1
}

if ! db_exists; then
  echo "==> creating ${DB_NAME} (base, sale, ncollection_core)"
  "${ODOO_RUN[@]}" odoo -d "${DB_NAME}" -i base,sale,ncollection_core \
    --without-demo=True --stop-after-init --no-http \
    "${DB_ARGS[@]}" --log-level=warn
else
  echo "==> reusing existing ${DB_NAME}"
fi

echo "==> seeding to ${ROWS} sale.order.line rows (idempotent)"
AGG_BENCH_ROWS="${ROWS}" "${ODOO_RUN[@]}" \
  env AGG_BENCH_ROWS="${ROWS}" odoo shell -d "${DB_NAME}" \
  "${DB_ARGS[@]}" --no-http --log-level=warn \
  < scripts/perf/seed_aggregation_data.py

echo "==> measuring"
mkdir -p "${RESULTS_DIR}"
bench_output="$("${ODOO_RUN[@]}" odoo shell -d "${DB_NAME}" \
  "${DB_ARGS[@]}" --no-http --log-level=warn \
  < scripts/perf/bench_aggregation.py)"

echo "${bench_output}" | grep -v -- '---AGGBENCH-JSON'

# Capture the JSON block host-side: scripts/ is not mounted into the container,
# so the benchmark cannot write this file itself.
if echo "${bench_output}" | grep -q -- '---AGGBENCH-JSON-BEGIN---'; then
  echo "${bench_output}" \
    | sed -n '/---AGGBENCH-JSON-BEGIN---/,/---AGGBENCH-JSON-END---/p' \
    | sed '1d;$d' > "${RESULTS_FILE}"
  echo "==> wrote ${RESULTS_FILE}"
else
  echo "FATAL: benchmark produced no JSON block — measurement failed." >&2
  exit 1
fi

# Fail loud on a blown budget. Never '|| true' a gate you then report on (R-005).
if grep -q '"all_within_budget": false' "${RESULTS_FILE}"; then
  echo "FAIL: at least one aggregation exceeded the ${BUDGET_MS:-500}ms budget." >&2
  exit 1
fi
echo "==> PASS: every aggregation within budget"
