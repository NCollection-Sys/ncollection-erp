#!/usr/bin/env bash
# Fully PRIVATE stack for the AI chat widget browser acceptance: its own Postgres, the repo's mock-provider gateway
# satellite and Odoo, on their own Docker network. It touches neither the shared database server nor any shared
# service. The gateway signing key is generated here, lives only in the environment of the two disposable containers
# and is never written to a file or printed.
#   usage: run_ai_web.sh up | clean         (OCA_DIR defaults to <repo>/oca; host port 8169 on 127.0.0.1)
set -u
winpath() { (cd "$1" && { pwd -W 2>/dev/null || pwd; }); }
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
NETN=ncai_web_net; PG=ncai_pg; GW=ncai_gateway; OD=ncai_odoo; DB=uxaiweb
export MSYS_NO_PATHCONV=1
clean() {
  docker rm -f "$OD" "$GW" "$PG" >/dev/null 2>&1
  docker network rm "$NETN" >/dev/null 2>&1
  docker volume rm -f "${DB}_fs" >/dev/null 2>&1
}
[ "${1:-}" = clean ] && { clean; echo cleaned; exit 0; }
[ "${1:-}" = up ] || { echo "usage: $0 up|clean"; exit 2; }
clean
A="$(winpath "$ROOT/custom_addons")"; G="$(winpath "$ROOT/satellites/ai_gateway")"; O="$(winpath "${OCA_DIR:-$ROOT/oca}")"
export NC_AI_GATEWAY_KEY="$(head -c 48 /dev/urandom | base64 | tr -dc A-Za-z0-9 | head -c 40)"
REPOS=(account-financial-reporting account-financial-tools mis-builder reporting-engine queue server-auth server-tools server-ux helpdesk)
AP="/mnt/extra-addons$(printf ',/mnt/oca/%s' "${REPOS[@]}")"
C=(--db_host="$PG" --db_user=odoo --db_password=aitest-only --data-dir=/tmp/od --max-cron-threads=0 "--addons-path=$AP")
V=(-v "${DB}_fs:/tmp/od" -v "$A:/mnt/extra-addons:ro" -v "$O:/mnt/oca:ro")
docker network create "$NETN" >/dev/null
docker run -d --name "$PG" --network "$NETN" --memory 1g -e POSTGRES_USER=odoo -e POSTGRES_PASSWORD=aitest-only postgres:16 >/dev/null
docker run -d --name "$GW" --network "$NETN" --network-alias ai-gateway --memory 256m -v "$G:/app:ro" -w /app \
  -e NC_AI_GATEWAY_KEY -e NC_AI_PROVIDER=mock -e NC_AI_PORT=8080 -e PYTHONUNBUFFERED=1 python:3.12-slim python3 gateway.py >/dev/null
until docker exec "$PG" pg_isready -U odoo -d postgres >/dev/null 2>&1; do sleep 2; done
docker run --rm --user root -v "${DB}_fs:/tmp/od" --entrypoint chown odoo:19 -R odoo:odoo /tmp/od
run() { docker run --rm -i --memory 2000m --network "$NETN" -e NC_AI_GATEWAY_KEY -e HOST="$PG" -e USER=odoo -e PASSWORD=aitest-only "${V[@]}" odoo:19 "$@"; }
echo "== install"
run odoo -d "$DB" "${C[@]}" -i ncollection_ai --load-language=ar_001 --without-demo=all --no-http --stop-after-init --log-level=warn \
  > "${LOG_DIR:-/tmp}/uxaiweb.install.log" 2>&1
echo "rc=$?"; grep -a "ERROR\|CRITICAL\|Some modules" "${LOG_DIR:-/tmp}/uxaiweb.install.log" | head -3
echo "== seed"
run odoo shell -d "$DB" "${C[@]}" --no-http --log-level=error < "$HERE/ai_fixture.py" 2>&1 | grep -a "Traceback\|Error\|AIFIX"
echo "== serve"
docker run -d --name "$OD" --memory 2000m --network "$NETN" -p 127.0.0.1:8169:8069 -e NC_AI_GATEWAY_KEY -e HOST="$PG" -e USER=odoo -e PASSWORD=aitest-only "${V[@]}" odoo:19 \
  odoo -d "$DB" "${C[@]}" --db-filter="^$DB\$" --log-level=warn >/dev/null
for i in $(seq 1 40); do curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8169/web/login | grep -q 200 && break; sleep 3; done
echo served; unset NC_AI_GATEWAY_KEY
