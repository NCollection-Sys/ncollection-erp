#!/usr/bin/env bash
# ============================================================================
#  deploy.sh — deploy (or re-deploy) the NCollection staging stack   (P2-T07)
# ============================================================================
#  Runs ON the staging server, from the repo checkout at /opt/ncollection.
#  Idempotent: re-running with the same IMAGE_TAG is a no-op restart on the
#  same image. Records the tag it replaces so rollback.sh can find it.
#
#  Usage:  IMAGE_TAG=sha-<gitsha> ./scripts/deploy/deploy.sh
#  The deploy-staging workflow calls this after syncing the repo to the commit
#  whose image it just pushed. See docs/markdown/RUNBOOK_STAGING.md.
# ============================================================================
set -euo pipefail

REPO_DIR="${NC_STAGING_DIR:-/opt/ncollection}"
IMAGE_TAG="${IMAGE_TAG:-develop}"
COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.staging.yml)

cd "$REPO_DIR"

# Remember the tag we are about to REPLACE (if any) so rollback has a target.
# First-ever deploy has no prior tag — that is fine, rollback simply refuses.
prev_tag="$(cat .last_deployed_tag 2>/dev/null || true)"
if [ -n "${prev_tag:-}" ] && [ "$prev_tag" != "$IMAGE_TAG" ]; then
  printf '%s\n' "$prev_tag" > .previous_deployed_tag
fi

echo "==> Deploying image tag: $IMAGE_TAG"
export IMAGE_TAG
"${COMPOSE[@]}" pull odoo
"${COMPOSE[@]}" up -d --remove-orphans

# Record the now-current tag for the NEXT rollback, only after a clean up.
printf '%s\n' "$IMAGE_TAG" > .last_deployed_tag

# Verify the stack actually came up before declaring success.
"$(dirname "$0")/smoke-test.sh"

echo "==> Deploy complete: $IMAGE_TAG"
