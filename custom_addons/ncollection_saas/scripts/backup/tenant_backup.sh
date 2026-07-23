#!/usr/bin/env bash
# ============================================================================
#  tenant_backup.sh — per-tenant logical backup  (ticket P2-T05)
# ============================================================================
#  §5.1 logical-dump layer: pg_dump (custom) + a tar of the tenant filestore
#  (attachments live on disk, NOT in the DB), bundled + aes-256-cbc encrypted.
#  Runs INSIDE a container with DB access + the odoo_data filestore mounted
#  (invoked by the ncollection.backup engine, off the HTTP workers).
#
#  Usage:  tenant_backup.sh <tenant_db> <daily|weekly|monthly>
#  Prints, for the engine to parse:  RESULT_PATH=<file>   RESULT_BYTES=<n>
# ============================================================================
set -euo pipefail

DB="${1:?tenant database name required}"
TYPE="${2:-daily}"
BACKUP_DIR="${NC_BACKUP_DIR:-/var/lib/odoo/backups}"
FILESTORE="${NC_FILESTORE:-/var/lib/odoo/filestore}"
DBHOST="${HOST:-db}"
DBUSER="${USER:-odoo}"
: "${TENANT_BACKUP_CIPHER_PASS:?encryption passphrase (TENANT_BACKUP_CIPHER_PASS) required}"
export PGPASSWORD="${PASSWORD:-odoo}"

ts="$(date -u +%Y%m%dT%H%M%SZ)"
work="$BACKUP_DIR/$DB"
base="${DB}_${TYPE}_${ts}"
mkdir -p "$work"

# 1. Database dump (custom format → parallel/selective restore).
pg_dump --format=custom -h "$DBHOST" -U "$DBUSER" -d "$DB" -f "$work/$base.dump"

# 2. Filestore tar (attachments). Empty placeholder if the tenant has none yet.
if [ -d "$FILESTORE/$DB" ]; then
  tar czf "$work/$base.filestore.tar.gz" -C "$FILESTORE" "$DB"
else
  tar czf "$work/$base.filestore.tar.gz" -C "$FILESTORE" --files-from /dev/null
fi

# 3. Bundle both, then encrypt at rest (§5).
tar cf "$work/$base.tar" -C "$work" "$base.dump" "$base.filestore.tar.gz"
openssl enc -aes-256-cbc -salt -pbkdf2 -pass env:TENANT_BACKUP_CIPHER_PASS \
  -in "$work/$base.tar" -out "$work/$base.tar.enc"
rm -f "$work/$base.dump" "$work/$base.filestore.tar.gz" "$work/$base.tar"

# 4. Off-site upload (prod). TENANT_BACKUP_UPLOAD_CMD is an executable taking the
#    encrypted file path (e.g. an rclone/aws wrapper) — operator-provided so no
#    specific CLI is baked into the image. Dev/test keeps the local copy only.
if [ -n "${TENANT_BACKUP_UPLOAD_CMD:-}" ]; then
  "$TENANT_BACKUP_UPLOAD_CMD" "$work/$base.tar.enc"
fi

echo "RESULT_PATH=$work/$base.tar.enc"
echo "RESULT_BYTES=$(wc -c < "$work/$base.tar.enc" | tr -d ' ')"
