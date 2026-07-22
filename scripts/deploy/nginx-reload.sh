#!/usr/bin/env bash
# ============================================================================
#  nginx-reload.sh — validate config, then GRACEFULLY reload nginx   (P2-T06)
# ============================================================================
#  Runs on the server after a custom-domain block is rendered into
#  nginx/conf.d/tenants/. Tests the config first (`nginx -t`) and only reloads
#  on success, so a bad render never takes the edge down. Graceful reload =
#  zero dropped connections.
#
#  Kept host-side ON PURPOSE: Odoo never reloads nginx itself (that would need
#  the Docker socket, which P2-T08 restricts). The platform records the desired
#  domains in ncollection.domain; this script applies them.
# ============================================================================
set -euo pipefail

COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.prod.yml)
SVC="${NGINX_SERVICE:-nginx}"

echo "==> Validating nginx config"
"${COMPOSE[@]}" exec -T "$SVC" nginx -t

echo "==> Reloading nginx (graceful)"
"${COMPOSE[@]}" exec -T "$SVC" nginx -s reload

echo "==> nginx reloaded"
