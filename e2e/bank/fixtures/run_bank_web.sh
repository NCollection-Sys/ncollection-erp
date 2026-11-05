#!/usr/bin/env bash
# Disposable web-enabled Odoo on db uxbankweb (install, seed, serve on host port 8169).
# Stop + clean: docker rm -f uxbankweb_runner; dropdb uxbankweb; docker volume rm uxbankweb_fs
NET="$(docker network ls --format "{{.Name}}" | grep -m1 "_nc_dbplane$")"; [ -n "$NET" ] || { echo "no *_nc_dbplane network: start the stack first"; exit 1; }
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
A="$(cd "$ROOT/custom_addons" && pwd -W)"
OCA="${OCA_DIR:-$ROOT/oca}"; O="$(cd "$OCA" && pwd -W)"
DBC="$(cd /d/Projects/ncollection-erp && docker compose ps -q db)"
DB=uxbankweb
AP=/mnt/extra-addons,/mnt/oca/account-financial-reporting,/mnt/oca/account-financial-tools,/mnt/oca/mis-builder,/mnt/oca/reporting-engine,/mnt/oca/queue,/mnt/oca/server-auth,/mnt/oca/server-tools,/mnt/oca/server-ux,/mnt/oca/helpdesk,/mnt/new/account-reconcile,/mnt/new/bank-statement-import
MODS=sale,purchase,ncollection_core,ncollection_branding,ncollection_account_core,ncollection_account_reports,ncollection_account_dashboard,ncollection_bank_reconciliation
C="--db_host=db --db_user=odoo --db_password=odoo --data-dir=/tmp/od --max-cron-threads=0 --addons-path=$AP"
LOG="${TMPDIR:-/tmp}/uxbankweb"
docker rm -f ${DB}_runner >/dev/null 2>&1
docker exec "$DBC" dropdb -U odoo --if-exists $DB; docker volume rm -f ${DB}_fs >/dev/null 2>&1
MSYS_NO_PATHCONV=1 docker run --rm --user root -v ${DB}_fs:/tmp/od --entrypoint chown odoo:19 -R odoo:odoo /tmp/od
V=(-v ${DB}_fs:/tmp/od -v "$A:/mnt/extra-addons:ro" -v "D:/Projects/ncollection-erp/oca:/mnt/oca:ro" -v "$O:/mnt/new:ro")
run() { MSYS_NO_PATHCONV=1 docker run --rm -i --memory 2000m --network "$NET" "${V[@]}" odoo:19 "$@"; }
echo "== install"; run odoo -d $DB $C -i $MODS --load-language=ar_001 --without-demo=all --no-http --stop-after-init --log-level=warn > "$LOG.install.log" 2>&1; echo rc=$?
grep -a "ERROR\|CRITICAL\|Some modules" "$LOG.install.log" | head -3
echo "== seed"; run odoo shell -d $DB $C --no-http --log-level=error < "$HERE/bank_fixture.py" 2>&1 | grep -a "Traceback\|Error\|BANKFIX"
printf "mods=env['ir.module.module'].search([('name','in',['ncollection_bank_reconciliation','account_reconcile_oca'])])\nmods._update_translations(['ar_001'])\nenv.registry.clear_all_caches()\nenv.cr.commit()\nprint('TR')\n" | run odoo shell -d $DB $C --no-http --log-level=error 2>&1 | grep -a "TR\|Error" | head -3
echo "== serve"
MSYS_NO_PATHCONV=1 docker run -d --name ${DB}_runner --memory 2000m --network bridge -p 8169:8069 "${V[@]}" odoo:19 odoo -d $DB $C --db-filter="^$DB\$" --log-level=warn >/dev/null
docker network connect "$NET" ${DB}_runner
echo served
