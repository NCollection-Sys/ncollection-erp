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
#  What this does: for every database on the server, makes the host of its web.base.url resolve to where that URL is
#  really served (no port -> the nginx edge, :8069 -> loopback), plus the two conventional names <db>.localhost and
#  <db>.<base domain>, in the container's /etc/hosts. Idempotent (second run adds nothing).
#  /etc/hosts of a container is NOT persistent across a container recreate (and Docker can rewrite it on a network
#  change without one), so re-run it after `docker compose up --force-recreate`; `make doctor` fetches each
#  tenant's own base URL from inside the container and says so when that fails.
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

# The public tenant URL is <db>.<base domain> on port 80 (the nginx edge) — ncollection.tenant._nc_public_origin(),
# the same name the browser opens and web.base.url holds. Inside the odoo container that name must reach the EDGE,
# not loopback (nothing listens on :80 there), so it is mapped to the nginx container's address. The older
# <db>.localhost:8069 form is kept for tenants that still carry it.
BASE_DOMAIN="${NC_BASE_DOMAIN:-}"
if [ -z "$BASE_DOMAIN" ]; then
  BASE_DOMAIN=$("${DC[@]}" exec -T db psql -U "$DB_USER" -d ncollection -Atc \
    "select value from ir_config_parameter where key = 'ncollection_saas.base_domain'" 2>/dev/null | tr -d '\r' | head -1 || true)
fi
BASE_DOMAIN="${BASE_DOMAIN:-ncollection.localhost}"
nginx=$("${DC[@]}" ps -q nginx 2>/dev/null || true)
edge_ip=""
if [ -n "$nginx" ]; then
  edge_ip=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' "$nginx" | awk '{print $1}')
fi

# map <ip> <host>: make exactly one line for host, pointing at ip (re-pointed if the edge's address changed).
# /etc/hosts of a container is a bind mount: it is rewritten in place, never replaced.
map() {
  local ip="$1" host="$2"
  if docker exec "$odoo" grep -qE "^${ip//./\.}[[:space:]]+${host//./\.}([[:space:]]|\$)" /etc/hosts; then
    return 1
  fi
  docker exec -u 0 "$odoo" bash -c "grep -vE '(^|[[:space:]])${host//./\.}([[:space:]]|\$)' /etc/hosts > /tmp/hosts.new; \
    printf '%s %s\n' '$ip' '$host' >> /tmp/hosts.new; cat /tmp/hosts.new > /etc/hosts"
  return 0
}

# The table of what must resolve to what, built in ONE pass so that a second run changes nothing:
#   1. the two conventional names of every tenant (defaults), then
#   2. the host of each tenant's OWN web.base.url, which overrides them. That URL is what wkhtmltopdf really fetches, and
#      its PORT decides the target: no port = the public edge (nginx, :80), an explicit port (:8069) = odoo itself on
#      loopback. A tenant opened at http://<db>.localhost (no port) is the case a fixed "<db>.localhost -> loopback"
#      gets wrong: nothing listens on :80 inside the container, the stylesheet fetch is refused and the PDF is unstyled.
declare -A want=()
for n in $names; do
  want["$n.localhost"]="127.0.0.1"
  [ -n "$edge_ip" ] && want["$n.$BASE_DOMAIN"]="$edge_ip"
  # a read, not a state change: a database that is not an Odoo one simply has no such table
  url=$("${DC[@]}" exec -T db psql -U "$DB_USER" -d "$n" -Atc \
    "select value from ir_config_parameter where key = 'web.base.url'" 2>/dev/null | tr -d '\r' | head -1 || true)
  [ -z "$url" ] && continue
  hostport="${url#*://}"; hostport="${hostport%%/*}"; host="${hostport%%:*}"
  case "$host" in *.localhost|localhost) ;; *) continue ;; esac      # a real, resolvable domain is left alone
  if [ "$hostport" = "$host" ]; then
    if [ -n "$edge_ip" ]; then want["$host"]="$edge_ip"; fi
  else
    want["$host"]="127.0.0.1"
  fi
done

added=0
for host in "${!want[@]}"; do
  map "${want[$host]}" "$host" && added=$((added + 1))
done
if [ -z "$edge_ip" ]; then
  echo "⚠️  nginx container not found: tenants served on the edge (no port in web.base.url) were NOT mapped (their PDFs stay unstyled)" >&2
fi
echo "✅ pdf-hosts: $added added, ${#want[@]} tenant hostnames resolvable inside the odoo container (edge ${edge_ip:-none}, base domain $BASE_DOMAIN)"
