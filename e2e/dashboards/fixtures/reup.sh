#!/usr/bin/env bash
# Upgrade the DISPOSABLE uxbrowse db with the current tree and restart its own server (never the shared stack).
# Usage: reup.sh <module[,module...]>     (OCA_DIR as in run_browse.sh)
set -u
winpath() { (cd "$1" && { pwd -W 2>/dev/null || pwd; }); }   # a Docker-for-Windows mount path, or a plain one
[ -n "${1:-}" ] || { echo "usage: $0 <modules>"; exit 2; }
NET="$(docker network ls --format "{{.Name}}" | grep -m1 "_nc_dbplane$")"
[ -n "$NET" ] || { echo "no *_nc_dbplane network: start the stack first"; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
A="$(winpath "$ROOT/custom_addons")"
O="$(winpath "${OCA_DIR:-$ROOT/oca}")"
CONF="$(winpath "$ROOT/config")/odoo.conf"
C=(--db_host=db --db_user=odoo --db_password=odoo --data-dir=/tmp/od --max-cron-threads=0 --no-http)
V=(-v uxbrowse_fs:/tmp/od -v "$A:/mnt/extra-addons:ro" -v "$O:/mnt/oca-addons:ro" -v "$CONF:/etc/odoo/odoo.conf:ro")
run() { MSYS_NO_PATHCONV=1 docker run --rm -i --memory 2500m --network "$NET" "${V[@]}" odoo:19 "$@"; }
docker stop uxbrowse_runner >/dev/null
run odoo -d uxbrowse "${C[@]}" -u "$1" --stop-after-init --log-level=warn 2>&1 | grep -a "ERROR\|CRITICAL" | head -3
printf "mods=env['ir.module.module'].search([('name','like','ncollection_%%')])\nmods._update_translations(['ar_001'])\nenv.registry.clear_all_caches()\nenv.cr.commit()\nprint('TR-DONE')\n" \
  | run odoo shell -d uxbrowse "${C[@]}" --log-level=error 2>&1 | grep -a "TR-DONE\|Error"
docker start uxbrowse_runner >/dev/null
sleep 20
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8169/web/login
