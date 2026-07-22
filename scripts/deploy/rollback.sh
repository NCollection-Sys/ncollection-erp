#!/usr/bin/env bash
# ============================================================================
#  rollback.sh — roll staging back to the previous image tag        (P2-T07)
# ============================================================================
#  With no argument, rolls back to the tag recorded by the last deploy
#  (.previous_deployed_tag). Pass an explicit tag to target any known image:
#      ./scripts/deploy/rollback.sh sha-1a2b3c4d5e6f
#  Reuses deploy.sh, so the rollback is verified by the same smoke test.
# ============================================================================
set -euo pipefail

REPO_DIR="${NC_STAGING_DIR:-/opt/ncollection}"
cd "$REPO_DIR"

target="${1:-}"
if [ -z "$target" ]; then
  target="$(cat .previous_deployed_tag 2>/dev/null || true)"
fi

if [ -z "${target:-}" ]; then
  echo "!! No rollback target. Pass a tag (rollback.sh sha-<gitsha>) or run at" >&2
  echo "   least one prior deploy so .previous_deployed_tag exists." >&2
  exit 1
fi

echo "==> Rolling back to image tag: $target"
IMAGE_TAG="$target" ./scripts/deploy/deploy.sh
echo "==> Rollback complete: $target"
