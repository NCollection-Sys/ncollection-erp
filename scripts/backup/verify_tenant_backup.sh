#!/usr/bin/env bash
# ============================================================================
#  verify_tenant_backup.sh — prove the P2-T05 acceptance  (local)
# ============================================================================
#  "backup of a tenant restores to a working workspace including attachments."
#  Sets up a tenant DB with a row + a filestore attachment, runs the real
#  backup + restore engine scripts, and asserts BOTH the data and the
#  attachment survive into a scratch DB. Runs against the dev stack.
#
#  Off-site S3/B2 upload is the operator step (env-driven); this proves the
#  encrypt → restore round-trip locally.
# ============================================================================
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 1

DC=(docker compose -f docker-compose.yml -f docker-compose.dev.yml)
SCR=/mnt/extra-addons/ncollection_saas/scripts/backup
CIPHER="${TENANT_BACKUP_CIPHER_PASS:-verify-local-pass}"
SRC=vtbsrc
DST=vtbrestore

pass=0
fail=0
ok(){ echo "  ✅ $1"; pass=$((pass + 1)); }
no(){ echo "  ❌ $1"; fail=$((fail + 1)); }

cleanup(){
  "${DC[@]}" exec -T db psql -U odoo -d postgres -tAc \
    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname IN ('$SRC','$DST')" >/dev/null 2>&1
  "${DC[@]}" exec -T db dropdb -U odoo --if-exists "$SRC" >/dev/null 2>&1
  "${DC[@]}" exec -T db dropdb -U odoo --if-exists "$DST" >/dev/null 2>&1
  "${DC[@]}" exec -T odoo rm -rf \
    "/var/lib/odoo/filestore/$SRC" "/var/lib/odoo/filestore/$DST" \
    "/var/lib/odoo/backups/$SRC" >/dev/null 2>&1
}
cleanup   # start clean

echo "==> set up tenant '$SRC' with a row + a filestore attachment"
"${DC[@]}" exec -T db createdb -U odoo "$SRC"
"${DC[@]}" exec -T db psql -U odoo -d "$SRC" -c \
  "CREATE TABLE workspace(note text); INSERT INTO workspace VALUES('live-workspace-data');" >/dev/null
"${DC[@]}" exec -T odoo bash -c \
  "mkdir -p /var/lib/odoo/filestore/$SRC/ab && printf 'INVOICE-PDF-BYTES' > /var/lib/odoo/filestore/$SRC/ab/att"

echo "==> back up (pg_dump + filestore tar + encrypt)"
out="$("${DC[@]}" exec -T -e TENANT_BACKUP_CIPHER_PASS="$CIPHER" odoo bash "$SCR/tenant_backup.sh" "$SRC" daily)"
path="$(printf '%s\n' "$out" | grep '^RESULT_PATH=' | cut -d= -f2 | tr -d '\r')"
if [ -n "$path" ]; then ok "backup produced $path"; else no "backup produced no file"; fi

echo "==> restore to scratch '$DST'"
"${DC[@]}" exec -T -e TENANT_BACKUP_CIPHER_PASS="$CIPHER" odoo bash "$SCR/tenant_restore.sh" "$path" "$DST" >/dev/null

echo "==> assert data + attachment survived"
row="$("${DC[@]}" exec -T db psql -U odoo -d "$DST" -tAc "SELECT note FROM workspace" | tr -d '[:space:]')"
if [ "$row" = "live-workspace-data" ]; then ok "DB data restored"; else no "DB data missing (got '$row')"; fi
att="$("${DC[@]}" exec -T odoo cat "/var/lib/odoo/filestore/$DST/ab/att" 2>/dev/null)"
if [ "$att" = "INVOICE-PDF-BYTES" ]; then ok "filestore attachment restored"; else no "attachment missing"; fi

cleanup
echo "------------------------------------------------------------"
echo "RESULT: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
