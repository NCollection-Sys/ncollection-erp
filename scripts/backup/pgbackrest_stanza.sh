#!/usr/bin/env bash
# ============================================================================
#  pgbackrest_stanza.sh — create + verify the pgBackRest stanza  (P2-T04)
# ============================================================================
#  One-time (idempotent) setup: initialises the repo for the `ncollection`
#  stanza and proves archiving works end-to-end (`check` pushes a test WAL).
#  Run once after the backup overlay is up. Requires PGBACKREST_REPO1_CIPHER_PASS.
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")/../.." || exit 1

DC=(docker compose -f docker-compose.yml -f docker-compose.backup.yml)

echo "==> stanza-create (idempotent)"
"${DC[@]}" exec -T -u postgres db pgbackrest --stanza=ncollection stanza-create

echo "==> check (archives a test WAL segment)"
"${DC[@]}" exec -T -u postgres db pgbackrest --stanza=ncollection check

echo "✅ stanza 'ncollection' ready and archiving verified"
