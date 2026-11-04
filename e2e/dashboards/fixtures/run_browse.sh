#!/usr/bin/env bash
# Disposable web-enabled Odoo on db uxbrowse (installs, seeds, then serves on host port 8169). Stop: docker rm -f uxbrowse_runner
NET="$(docker network ls --format "{{.Name}}" | grep -m1 "_nc_dbplane$")"; [ -n "$NET" ] || { echo "no *_nc_dbplane network: start the stack first"; exit 1; }
set -u
S="C:/Users/aibra/AppData/Local/Temp/claude/26049b1f41c4ef67/8da1f7a0-9ce9-41cf-8938-8f1aa741814f/scratchpad"
A="$(cd $S/wt2/custom_addons && pwd -W)"
DB=uxbrowse
MODS=sale,purchase,stock,stock_account,hr,spreadsheet_dashboard_account,spreadsheet_dashboard_sale,ncollection_core,ncollection_branding,ncollection_account_core,ncollection_account_analytics,ncollection_account_reports,ncollection_account_dashboard,ncollection_sale_documents,ncollection_purchase_documents,ncollection_dashboard_export,ncollection_subscription
C="--db_host=db --db_user=odoo --db_password=odoo --data-dir=/tmp/od --max-cron-threads=0"
docker rm -f ${DB}_runner >/dev/null 2>&1
docker exec c9bbd9617585 dropdb -U odoo --if-exists $DB; docker volume rm -f uxbrowse_fs >/dev/null 2>&1
MSYS_NO_PATHCONV=1 docker run --rm --user root -v uxbrowse_fs:/tmp/od --entrypoint chown odoo:19 -R odoo:odoo /tmp/od
run() { MSYS_NO_PATHCONV=1 docker run --rm -i --memory 2500m --network $NET -v uxbrowse_fs:/tmp/od -v "$A:/mnt/extra-addons:ro" -v "D:/Projects/ncollection-erp/oca:/mnt/oca-addons:ro" -v "D:/Projects/ncollection-erp/config/odoo.conf:/etc/odoo/odoo.conf:ro" odoo:19 "$@"; }
echo "== install"; run odoo -d $DB $C -i $MODS --load-language=ar_001 --without-demo=all --no-http --stop-after-init --log-level=warn > "$S/browse_install.log" 2>&1; echo rc=$?; grep -a "ERROR\|CRITICAL\|Some modules" "$S/browse_install.log" | head -3
echo "== seed"; run odoo shell -d $DB $C --no-http --log-level=error < "$S/browse_fixture.py" 2>&1 | grep -a "Traceback\|Error\|SEED\|BROWSE" | head
echo "== seed premium"; run odoo shell -d $DB $C --no-http --log-level=error < "$S/browse_premium.py" 2>&1 | grep -a "Traceback|Error|PREMIUM" | head
echo "== seed users"; run odoo shell -d $DB $C --no-http --log-level=error < "$S/browse_users.py" 2>&1 | tail -3
printf "mods=env['ir.module.module'].search([('name','like','ncollection_%%')])\nmods._update_translations(['ar_001'])\nenv.registry.clear_all_caches()\nenv.cr.commit()\nprint('TR')\n" | run odoo shell -d $DB $C --no-http --log-level=error 2>&1 | grep -a "TR\|Error" | head -3
echo "== serve"
MSYS_NO_PATHCONV=1 docker run -d --name ${DB}_runner --memory 2500m --network bridge -p 8169:8069 -v uxbrowse_fs:/tmp/od -v "$A:/mnt/extra-addons:ro" -v "D:/Projects/ncollection-erp/oca:/mnt/oca-addons:ro" -v "D:/Projects/ncollection-erp/config/odoo.conf:/etc/odoo/odoo.conf:ro" odoo:19 odoo -d $DB $C --db-filter="^$DB\$" --log-level=warn >/dev/null
docker network connect $NET ${DB}_runner
echo served
