#!/usr/bin/env bash
# Upgrade the DISPOSABLE uxbrowse db with the current worktree and restart its server (never the shared stack).
NET="$(docker network ls --format "{{.Name}}" | grep -m1 "_nc_dbplane$")"; [ -n "$NET" ] || { echo "no *_nc_dbplane network: start the stack first"; exit 1; }
S="C:/Users/aibra/AppData/Local/Temp/claude/26049b1f41c4ef67/8da1f7a0-9ce9-41cf-8938-8f1aa741814f/scratchpad"
A="$(cd $S/wt2/custom_addons && pwd -W)"
C="--db_host=db --db_user=odoo --db_password=odoo --data-dir=/tmp/od --max-cron-threads=0 --no-http"
run() { MSYS_NO_PATHCONV=1 docker run --rm -i --memory 2500m --network $NET -v uxbrowse_fs:/tmp/od -v "$A:/mnt/extra-addons:ro" -v "D:/Projects/ncollection-erp/oca:/mnt/oca-addons:ro" -v "D:/Projects/ncollection-erp/config/odoo.conf:/etc/odoo/odoo.conf:ro" odoo:19 "$@"; }
docker stop uxbrowse_runner >/dev/null
run odoo -d uxbrowse $C -u "$1" --stop-after-init --log-level=warn 2>&1 | grep -a "ERROR\|CRITICAL" | head -3
printf "mods=env['ir.module.module'].search([('name','like','ncollection_%%')])\nmods._update_translations(['ar_001'])\nenv.registry.clear_all_caches()\nenv.cr.commit()\nprint('TR-DONE')\n" | run odoo shell -d uxbrowse $C --log-level=error 2>&1 | grep -a "TR-DONE\|Error"
docker start uxbrowse_runner >/dev/null; sleep 20; curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8169/web/login
