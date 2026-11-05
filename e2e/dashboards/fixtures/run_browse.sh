#!/usr/bin/env bash
# Disposable web-enabled Odoo on db uxbrowse (installs, seeds, then serves on host port 8169).
# Stop: docker rm -f uxbrowse_runner. Never touches the shared stack's databases.
#   OCA_DIR      the aggregated ./oca (default: <repo>/oca; a fresh worktree has none - point it at a checkout's)
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
CONF="$(winpath "$ROOT/config")/odoo.conf"
DBC="$(cd "${COMPOSE_DIR:-$ROOT}" && docker compose ps -q db)"
[ -n "$DBC" ] || { echo "no running db service under ${COMPOSE_DIR:-$ROOT}"; exit 1; }
LOG_DIR="${LOG_DIR:-$(mktemp -d)}"
DB=uxbrowse
MODS=sale,purchase,stock,stock_account,hr,spreadsheet_dashboard_account,spreadsheet_dashboard_sale,ncollection_core,ncollection_branding,ncollection_account_core,ncollection_account_analytics,ncollection_account_reports,ncollection_account_dashboard,ncollection_sale_documents,ncollection_purchase_documents,ncollection_dashboard_export,ncollection_subscription
C=(--db_host=db --db_user=odoo --db_password=odoo --data-dir=/tmp/od --max-cron-threads=0)
V=(-v uxbrowse_fs:/tmp/od -v "$A:/mnt/extra-addons:ro" -v "$O:/mnt/oca-addons:ro" -v "$CONF:/etc/odoo/odoo.conf:ro")
docker rm -f "${DB}_runner" >/dev/null 2>&1
docker exec "$DBC" dropdb -U odoo --if-exists "$DB"
docker volume rm -f uxbrowse_fs >/dev/null 2>&1
MSYS_NO_PATHCONV=1 docker run --rm --user root -v uxbrowse_fs:/tmp/od --entrypoint chown odoo:19 -R odoo:odoo /tmp/od
run() { MSYS_NO_PATHCONV=1 docker run --rm -i --memory 2500m --network "$NET" "${V[@]}" odoo:19 "$@"; }
echo "== install"
run odoo -d "$DB" "${C[@]}" -i "$MODS" --load-language=ar_001 --without-demo=all --no-http --stop-after-init \
  --log-level=warn > "$LOG_DIR/browse_install.log" 2>&1
echo "rc=$?"
grep -a "ERROR\|CRITICAL\|Some modules" "$LOG_DIR/browse_install.log" | head -3
echo "== seed"; run odoo shell -d "$DB" "${C[@]}" --no-http --log-level=error < "$HERE/browse_fixture.py" 2>&1 | grep -a "Traceback\|Error\|SEED\|BROWSE" | head
echo "== seed premium"; run odoo shell -d "$DB" "${C[@]}" --no-http --log-level=error < "$HERE/browse_premium.py" 2>&1 | grep -a "Traceback\|Error\|PREMIUM" | head
echo "== seed users"; run odoo shell -d "$DB" "${C[@]}" --no-http --log-level=error < "$HERE/browse_users.py" 2>&1 | tail -3
printf "mods=env['ir.module.module'].search([('name','like','ncollection_%%')])\nmods._update_translations(['ar_001'])\nenv.registry.clear_all_caches()\nenv.cr.commit()\nprint('TR')\n" \
  | run odoo shell -d "$DB" "${C[@]}" --no-http --log-level=error 2>&1 | grep -a "TR\|Error" | head -3
echo "== serve"
MSYS_NO_PATHCONV=1 docker run -d --name "${DB}_runner" --memory 2500m --network bridge -p 8169:8069 "${V[@]}" odoo:19 \
  odoo -d "$DB" "${C[@]}" --db-filter="^$DB\$" --log-level=warn >/dev/null
docker network connect "$NET" "${DB}_runner"
echo served
