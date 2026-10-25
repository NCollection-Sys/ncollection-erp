#!/usr/bin/env bash
# shellcheck disable=SC2015  # the repo's A && ok || no reporting idiom
# ============================================================================
#  Does the production edge follow the configured domain? (balancecloud.ae readiness)
# ============================================================================
#  Renders nginx/conf.d/ncollection.prod.conf (an envsubst TEMPLATE) in the REAL nginx image once per domain, with a
#  throwaway self-signed certificate at the path the template derives from DOMAIN, and runs `nginx -t` on the result.
#  It proves, per domain:
#    * server_name is the apex AND every subdomain of THAT domain (one wildcard route, no per-tenant block);
#    * the certificate path follows the domain (live/<domain>/...);
#    * nothing else was substituted: nginx's own variables ($host, $scheme, ...) survive, and no ${DOMAIN} is left;
#    * no other domain is named anywhere in the rendered file (an old production domain would fail here);
#    * the forwarded-header snippet still hands Odoo the Host and the scheme (proxy_mode needs both).
#  Docker only; nothing is deployed, no DNS or certificate is touched.
#
#  usage: scripts/deploy/verify_edge_domain.sh [domain ...]      (default: balancecloud.ae and a second, unrelated one)
#  EDGE_TEMPLATE=<host path> renders another template instead (its self-test, test_verify_edge_domain.sh, feeds it a
#  broken one: the verdict must follow `nginx -t`'s own exit status, never a pipe's).
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")/../.."
export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL="*"          # Git Bash on Windows must not rewrite container paths

DOMAINS=("$@")
[ "${#DOMAINS[@]}" -gt 0 ] || DOMAINS=(balancecloud.ae example.org)
SRC="$(pwd -W 2>/dev/null || pwd)"
TEMPLATE="${EDGE_TEMPLATE:-$SRC/nginx/conf.d/ncollection.prod.conf}"
IMAGE="${EDGE_VERIFY_IMAGE:-nginx:1.27-alpine}"
fail=0

for domain in "${DOMAINS[@]}"; do
  case "$domain" in *[!a-z0-9.-]*|"") echo "REFUSING: '$domain' is not a plain domain name" >&2; exit 2 ;; esac
  echo "== $domain =="
  if docker run --rm -e DOMAIN="$domain" -e NGINX_ENVSUBST_FILTER='^DOMAIN$' \
       --add-host odoo:127.0.0.1 --add-host odoo-bus:127.0.0.1 \
       -v "$TEMPLATE:/etc/nginx/templates/default.conf.template:ro" \
       -v "$SRC/nginx/snippets:/etc/nginx/snippets:ro" \
       "$IMAGE" sh -ec '
         apk add --no-cache openssl >/dev/null 2>&1
         live="/etc/letsencrypt/live/$DOMAIN"; mkdir -p "$live"
         openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj "/CN=$DOMAIN" \
           -keyout "$live/privkey.pem" -out "$live/fullchain.pem" >/dev/null 2>&1
         /docker-entrypoint.d/20-envsubst-on-templates.sh >/dev/null
         conf=/etc/nginx/conf.d/default.conf
         names="$(grep -c "server_name \.$DOMAIN;" "$conf")"
         [ "$names" = "2" ] || { echo "  FAIL: expected 2 server_name .$DOMAIN blocks (http + https), found $names"; exit 1; }
         grep -q "ssl_certificate     $live/fullchain.pem;" "$conf" || { echo "  FAIL: certificate path does not follow DOMAIN"; exit 1; }
         grep -q "ssl_certificate_key $live/privkey.pem;" "$conf" || { echo "  FAIL: certificate key path does not follow DOMAIN"; exit 1; }
         ! grep -q "\${DOMAIN}" "$conf" || { echo "  FAIL: an unrendered \${DOMAIN} is left in the config"; exit 1; }
         grep -q "return 301 https://\$host\$request_uri;" "$conf" || { echo "  FAIL: nginx variables were clobbered by the substitution"; exit 1; }
         other="$(grep -v "^ *#" "$conf" | grep -oE "[a-z0-9-]+(\.[a-z0-9-]+)*\.(com|ae|org|net|io|localhost)\b" | grep -v "^$DOMAIN\$" | sort -u || true)"
         [ -z "$other" ] || { echo "  FAIL: the rendered config names another domain: $other"; exit 1; }
         grep -q "proxy_set_header Host \$host;" /etc/nginx/snippets/proxy.conf || { echo "  FAIL: Host is not forwarded"; exit 1; }
         grep -q "proxy_set_header X-Forwarded-Proto \$scheme;" /etc/nginx/snippets/proxy.conf || { echo "  FAIL: scheme is not forwarded"; exit 1; }
         # The verdict is nginx -t'"'"'s OWN exit status: piped into sed (no pipefail in this sh) a refused config
         # used to print OK and exit 0 (pre-commit review M4).
         if out="$(nginx -t 2>&1)"; then
           printf "%s\n" "$out" | sed "s/^/  nginx: /"
         else
           printf "%s\n" "$out" | sed "s/^/  nginx: /"
           echo "  FAIL: nginx -t refused the rendered configuration"; exit 1
         fi
         echo "  OK: server_name .$DOMAIN (apex + every subdomain), certificate live/$DOMAIN, forwarded Host + scheme, nginx -t passes"
       '; then :; else echo "  ❌ $domain"; fail=1; fi
done

[ "$fail" -eq 0 ] && echo "✅ the production edge follows DOMAIN for: ${DOMAINS[*]}" || { echo "❌ the production edge does NOT follow DOMAIN"; exit 1; }
