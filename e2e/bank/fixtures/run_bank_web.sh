#!/usr/bin/env bash
# Disposable web-enabled Odoo on db uxbankweb (install, seed, serve on host port 8169). Never the shared databases.
# Stop + clean: docker rm -f uxbankweb_runner; dropdb uxbankweb; docker volume rm uxbankweb_fs
#   OCA_DIR      aggregated ./oca WITH account-reconcile and bank-statement-import (default: <repo>/oca)
#   COMPOSE_DIR  the directory whose `docker compose` owns the running `db` service (default: <repo>)
#   LOG_DIR      where install logs go (default: a temp dir)
set -u
winpath() { (cd "$1" && { pwd -W 2>/dev/null || pwd; }); }   # a Docker-for-Windows mount path, or a plain one
NET="$(docker network ls --format "{{.Name}}" | grep -m1 "_nc_dbplane$")"
[ -n "$NET" ] || { echo "no *_nc_dbplane network: start the stack first"; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
A="$(winpath "$ROOT/custom_addons")"
O="$(winpath "${OCA_DIR:-$ROOT/oca}")"
DBC="$(cd "${COMPOSE_DIR:-$ROOT}" && docker compose ps -q db)"
[ -n "$DBC" ] || { echo "no running db service under ${COMPOSE_DIR:-$ROOT}"; exit 1; }
LOG_DIR="${LOG_DIR:-$(mktemp -d)}"
DB=uxbankweb
REPOS=(account-financial-reporting account-financial-tools mis-builder reporting-engine queue server-auth server-tools
       server-ux helpdesk account-reconcile bank-statement-import)
AP="/mnt/extra-addons$(printf ',/mnt/oca/%s' "${REPOS[@]}")"
MODS=sale,purchase,ncollection_core,ncollection_branding,ncollection_account_core,ncollection_account_reports,ncollection_account_dashboard,ncollection_bank_reconciliation
C=(--db_host=db --db_user=odoo --db_password=odoo --data-dir=/tmp/od --max-cron-threads=0 "--addons-path=$AP")
V=(-v "${DB}_fs:/tmp/od" -v "$A:/mnt/extra-addons:ro" -v "$O:/mnt/oca:ro")
docker rm -f "${DB}_runner" >/dev/null 2>&1
docker exec "$DBC" dropdb -U odoo --if-exists "$DB"
docker volume rm -f "${DB}_fs" >/dev/null 2>&1
MSYS_NO_PATHCONV=1 docker run --rm --user root -v "${DB}_fs:/tmp/od" --entrypoint chown odoo:19 -R odoo:odoo /tmp/od
run() { MSYS_NO_PATHCONV=1 docker run --rm -i --memory 2000m --network "$NET" "${V[@]}" odoo:19 "$@"; }
echo "== install"
run odoo -d "$DB" "${C[@]}" -i "$MODS" --load-language=ar_001 --without-demo=all --no-http --stop-after-init \
  --log-level=warn > "$LOG_DIR/uxbankweb.install.log" 2>&1
echo "rc=$?"
grep -a "ERROR\|CRITICAL\|Some modules" "$LOG_DIR/uxbankweb.install.log" | head -3
echo "== seed"
run odoo shell -d "$DB" "${C[@]}" --no-http --log-level=error < "$HERE/bank_fixture.py" 2>&1 | grep -a "Traceback\|Error\|BANKFIX"
run odoo shell -d "$DB" "${C[@]}" --no-http --log-level=error < "$HERE/bank_fixture_gaps.py" 2>&1 | grep -a "Traceback\|Error\|GAPFIX"
printf "mods=env['ir.module.module'].search([('name','in',['ncollection_bank_reconciliation','account_reconcile_oca'])])\nmods._update_translations(['ar_001'])\nenv.registry.clear_all_caches()\nenv.cr.commit()\nprint('TR')\n" \
  | run odoo shell -d "$DB" "${C[@]}" --no-http --log-level=error 2>&1 | grep -a "TR\|Error" | head -3
echo "== serve"
MSYS_NO_PATHCONV=1 docker run -d --name "${DB}_runner" --memory 2000m --network bridge -p 8169:8069 "${V[@]}" odoo:19 \
  odoo -d "$DB" "${C[@]}" --db-filter="^$DB\$" --log-level=warn >/dev/null
docker network connect "$NET" "${DB}_runner"
echo served
