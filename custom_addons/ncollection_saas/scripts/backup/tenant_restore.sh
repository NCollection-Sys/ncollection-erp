#!/usr/bin/env bash
# ============================================================================
#  tenant_restore.sh — restore a tenant backup to a SCRATCH db  (ticket P2-T05)
# ============================================================================
#  Decrypt the bundle → pg_restore into a fresh TARGET database → restore the
#  filestore under the TARGET db name. Never touches the live tenant: TARGET is
#  a scratch/staging DB chosen by the caller (the Restore wizard or the drill).
#
#  Usage:  tenant_restore.sh <backup_file.enc> <target_db>
#  Prints:  RESULT_DB=<target>   RESULT_ATTACHMENTS=<file count>
# ============================================================================
set -euo pipefail

ENC="${1:?encrypted backup file required}"
TARGET="${2:?target (scratch) database required}"
FILESTORE="${NC_FILESTORE:-/var/lib/odoo/filestore}"
DBHOST="${HOST:-db}"
DBUSER="${USER:-odoo}"
: "${TENANT_BACKUP_CIPHER_PASS:?encryption passphrase (TENANT_BACKUP_CIPHER_PASS) required}"
export PGPASSWORD="${PASSWORD:-odoo}"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

# 1. Decrypt + unbundle.
openssl enc -d -aes-256-cbc -pbkdf2 -pass env:TENANT_BACKUP_CIPHER_PASS \
  -in "$ENC" -out "$tmp/bundle.tar"
tar xf "$tmp/bundle.tar" -C "$tmp"
dump="$(find "$tmp" -maxdepth 1 -name '*.dump' | head -1)"
fs="$(find "$tmp" -maxdepth 1 -name '*.filestore.tar.gz' | head -1)"

# 2. Recreate the scratch DB and restore the dump into it. Terminate any
#    lingering sessions first (Odoo dev auto-connects to new DBs), else dropdb
#    fails with "database is being accessed by other users".
psql -h "$DBHOST" -U "$DBUSER" -d postgres -tAc \
  "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$TARGET' AND pid <> pg_backend_pid()" >/dev/null
dropdb -h "$DBHOST" -U "$DBUSER" --if-exists "$TARGET"
createdb -h "$DBHOST" -U "$DBUSER" "$TARGET"
# pg_restore returns 1 on NON-fatal "errors ignored" too — e.g. a newer pg_dump
# client emitting `SET transaction_timeout` that the pinned postgres:16 server
# rejects harmlessly. Judge success by whether the schema actually restored, not
# by the exit code alone.
set +e
pg_restore -h "$DBHOST" -U "$DBUSER" -d "$TARGET" --no-owner --no-privileges "$dump"
set -e
tables="$(psql -h "$DBHOST" -U "$DBUSER" -d "$TARGET" -tAc \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")"
if [ "${tables:-0}" -eq 0 ]; then
  echo "!! restore produced an empty database ($TARGET)" >&2
  exit 1
fi

# 3. Restore the filestore UNDER THE TARGET db name (the tar holds <origdb>/…).
attachments=0
mkdir -p "$FILESTORE"
rm -rf "${FILESTORE:?}/$TARGET"
if [ -s "$fs" ]; then
  ex="$tmp/fs"; mkdir -p "$ex"
  tar xzf "$fs" -C "$ex"
  origdir="$(find "$ex" -mindepth 1 -maxdepth 1 -type d | head -1)"
  if [ -n "$origdir" ]; then
    mv "$origdir" "$FILESTORE/$TARGET"
    attachments="$(find "$FILESTORE/$TARGET" -type f | wc -l | tr -d ' ')"
  fi
fi

echo "RESULT_DB=$TARGET"
echo "RESULT_ATTACHMENTS=$attachments"
