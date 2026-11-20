#!/usr/bin/env bash
# Read-only SaaS feature-parity check for ONE platform (see tenant_parity_check.py for what it proves).
#
#   scripts/dev/tenant_parity_check.sh <odoo container> <platform db> [odoo config inside the container]
#
# Exit 0 = PARITY_RESULT=PASS, 1 = FAIL, 2 = could not run. Writes nothing anywhere.
set -euo pipefail
container=${1:?usage: tenant_parity_check.sh <odoo container> <platform db> [conf]}
platform_db=${2:?usage: tenant_parity_check.sh <odoo container> <platform db> [conf]}
conf=${3:-/etc/odoo/odoo.conf}
here=$(cd "$(dirname "$0")" && pwd)
# The odoo image hands the database connection (HOST, PORT, USER, PASSWORD) to odoo through its entrypoint;
# `docker exec` bypasses the entrypoint, so the same values are passed explicitly inside the container (never printed).
# shellcheck disable=SC2016
out=$(docker exec -i -e NC_PARITY_DB="$platform_db" -e NC_PARITY_CONF="$conf" "$container" sh -c \
      'exec odoo shell -c "$NC_PARITY_CONF" -d "$NC_PARITY_DB" --db_host="${HOST:-db}" --db_port="${PORT:-5432}" \
       --db_user="${USER:-odoo}" --db_password="${PASSWORD:-}" --no-http --log-level=error' \
      < "$here/tenant_parity_check.py") || { echo "tenant_parity_check: odoo shell failed on $platform_db" >&2; exit 2; }
printf '%s\n' "$out" | grep -v '^PARITY_JSON='
result=$(printf '%s\n' "$out" | sed -n 's/^PARITY_RESULT=//p' | tail -1)
case "$result" in
  PASS) exit 0 ;;
  FAIL) exit 1 ;;
  *) echo "tenant_parity_check: no result line (did the script run?)" >&2; exit 2 ;;
esac
