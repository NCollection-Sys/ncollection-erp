#!/usr/bin/env bash
# ============================================================================
#  smoke-test.sh — prove the staging Odoo is alive after a deploy   (P2-T07)
# ============================================================================
#  Polls Odoo's built-in no-database health route (/web/health -> 200 OK).
#  Runs on the staging server against localhost:8069 by default; override
#  SMOKE_URL to probe through a public hostname once P2-T06 lands the edge.
# ============================================================================
set -euo pipefail

URL="${SMOKE_URL:-http://localhost:8069/web/health}"
RETRIES="${SMOKE_RETRIES:-30}"
SLEEP="${SMOKE_SLEEP:-5}"

echo "==> Smoke test: $URL (up to $((RETRIES * SLEEP))s)"
for i in $(seq 1 "$RETRIES"); do
  if curl -sf "$URL" >/dev/null; then
    echo "==> Healthy after ~$((i * SLEEP))s: $URL"
    exit 0
  fi
  sleep "$SLEEP"
done

echo "!! Smoke test FAILED — $URL did not return 200 within $((RETRIES * SLEEP))s" >&2
exit 1
