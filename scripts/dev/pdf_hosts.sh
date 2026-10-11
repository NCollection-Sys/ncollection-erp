#!/usr/bin/env bash
# ============================================================================
#  Let the dev Odoo container resolve <tenant>.localhost so PDFs come out styled
# ============================================================================
#  Symptom: a report PDF has no company header, no logo, no table borders and no
#  page margins, and the Odoo log says
#      wkhtmltopdf: Exit with code 1 due to network error: HostNotFoundError
#
#  Cause: wkhtmltopdf runs INSIDE the odoo container and fetches the report
#  stylesheet/header/logo from web.base.url (http://<tenant>.localhost:8069).
#  Browsers resolve *.localhost to loopback by themselves; the container's glibc
#  does not, so the fetch fails. Odoo still returns a PDF — an unstyled one — and
#  exits 0, which is why nothing visibly breaks. Production is unaffected: its
#  base URL is a real, resolvable hostname.
#
#  What this does: appends `127.0.0.1 <db>.localhost` to the container's
#  /etc/hosts for every database on the server. Idempotent (second run adds
#  nothing). /etc/hosts of a container is NOT persistent across a container
#  recreate, so re-run it after `docker compose up --force-recreate`.
#
#  Usage:  make pdf-hosts
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")/../.."   # repo root

DB_USER="${DB_USER:-odoo}"
DC=(docker compose -f docker-compose.yml -f docker-compose.dev.yml)

odoo=$("${DC[@]}" ps -q odoo)
db=$("${DC[@]}" ps -q db)
if [ -z "$odoo" ] || [ -z "$db" ]; then
  echo "❌ odoo/db container not running — run: make up" >&2
  exit 1
fi

names=$("${DC[@]}" exec -T db psql -U "$DB_USER" -d postgres -Atc \
  "select datname from pg_database where not datistemplate and datname <> 'postgres' order by 1")
if [ -z "$names" ]; then
  echo "❌ no databases found on the server" >&2
  exit 1
fi

added=0
for n in $names; do
  host="$n.localhost"
  if docker exec "$odoo" grep -qE "(^|[[:space:]])${host}([[:space:]]|$)" /etc/hosts; then
    continue
  fi
  docker exec -u 0 "$odoo" bash -c "printf '127.0.0.1 %s\n' '$host' >> /etc/hosts"
  added=$((added + 1))
done
echo "✅ pdf-hosts: $added added, $(echo "$names" | wc -l | tr -d ' ') tenant hostnames resolvable inside the odoo container"
