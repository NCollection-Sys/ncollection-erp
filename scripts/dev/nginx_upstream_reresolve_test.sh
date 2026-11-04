#!/usr/bin/env bash
# Does nginx follow the "odoo" upstream when that container comes back on ANOTHER address?
#
# THE INCIDENT THIS GUARDS. nginx resolved `server odoo:8069` once, at its own start. After `docker stop` / `docker start`
# of odoo and the provisioning runner, the runner was handed odoo's old address, so nginx sent every tenant to the runner
# (db-filter ^ncollection$): "Database not found" on every tenant, while http://localhost:8069 still worked.
#
# THE PROOF. A private network and private containers (nothing shared is touched): a stand-in "odoo" answers ODOO-A; it is
# removed, a stand-in "runner" takes its address, and "odoo" returns elsewhere answering ODOO-B. nginx, started with the
# committed dev config, must answer ODOO-B. Exit 0 = routing follows; 1 = it would reach the runner; 2 = harness problem.
#   usage: scripts/dev/nginx_upstream_reresolve_test.sh [path-to-conf (default nginx/conf.d/ncollection.dev.conf)]
set -u
ROOT="$(cd "$(dirname "$0")/../.." && { pwd -W 2>/dev/null || pwd; })"
CONF="${1:-$ROOT/nginx/conf.d/ncollection.dev.conf}"
[ -f "$CONF" ] || { echo "no such config: $CONF" >&2; exit 2; }
N="ncnginxresolve$$"
pause(){ sleep "$1" 2>/dev/null || powershell -NoProfile -c "Start-Sleep -Seconds $1"; }
cleanup(){ docker rm -f "${N}_odoo_a" "${N}_odoo_b" "${N}_runner" "${N}_nginx" >/dev/null 2>&1; docker network rm "$N" >/dev/null 2>&1; }
trap cleanup EXIT
cleanup
docker network create --subnet 172.29.251.0/29 "$N" >/dev/null || exit 2      # tiny: a freed address is reused at once
web(){ local alias=(); [ -n "$2" ] && alias=(--network-alias "$2")
  MSYS_NO_PATHCONV=1 docker run -d --name "$1" --network "$N" "${alias[@]}" python:3.12-slim sh -c \
    "mkdir -p /w/odoo && printf '%s' '$3' > /w/odoo/probe && cd /w && exec python -m http.server 8069" >/dev/null || exit 2; }
ask(){ docker exec "${N}_nginx" wget -qO- -T 5 --header "Host: t1.localhost" "http://127.0.0.1/odoo/probe" 2>/dev/null; }

web "${N}_odoo_a" odoo ODOO-A; pause 4
MSYS_NO_PATHCONV=1 docker run -d --name "${N}_nginx" --network "$N" \
  -v "$CONF:/etc/nginx/conf.d/default.conf:ro" -v "$ROOT/nginx/snippets:/etc/nginx/snippets:ro" \
  nginx:1.27-alpine >/dev/null || exit 2
pause 4
docker exec "${N}_nginx" nginx -t >/dev/null 2>&1 || { echo "config invalid:"; docker exec "${N}_nginx" nginx -t; exit 2; }
before="$(ask)"; echo "before the restart: $before"
[ "$before" = "ODOO-A" ] || { echo "harness problem: expected ODOO-A"; exit 2; }
docker rm -f "${N}_odoo_a" >/dev/null                              # odoo stops ...
web "${N}_runner" "" RUNNER                                         # ... the runner is handed its address ...
web "${N}_odoo_b" odoo ODOO-B                                       # ... odoo comes back on another one
pause 14                                                            # longer than the resolver's valid=10s
after="$(ask)"; echo "after the restart:  $after"
if [ "$after" = "ODOO-B" ]; then echo "PASS: nginx followed odoo to its new address"; exit 0; fi
echo "FAIL: nginx answered '$after' - tenants would reach the wrong container"; exit 1
