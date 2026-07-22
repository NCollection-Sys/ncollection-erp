#!/usr/bin/env bash
# ============================================================================
#  Enable branch protection — ONE COMMAND, for the day the org upgrades
# ============================================================================
#  Applies exactly the policy in docs/markdown/BRANCH_PROTECTION.md §2.
#
#  This CANNOT work on the current plan. Protected branches and rulesets require
#  Pro / Team / Enterprise for PRIVATE repositories; GitHub Free supports them
#  only on public repos. The script detects that and says so clearly rather than
#  dumping a raw 403.
#
#  Usage:
#    bash scripts/ci/enable_branch_protection.sh            # develop + main
#    bash scripts/ci/enable_branch_protection.sh develop    # one branch
# ============================================================================
set -euo pipefail

REPO="${REPO:-NCollection-Sys/ncollection-erp}"

# Blocking checks. security-scan is deliberately EXCLUDED — it is advisory
# (pip-audit/trivy) and must not gate a merge on a fresh advisory.
CHECKS=(lint architecture-guard test build verify)

branches=("$@")
if [ ${#branches[@]} -eq 0 ]; then
  branches=(develop main)
fi

command -v gh >/dev/null 2>&1 || {
  echo "ERROR: the GitHub CLI (gh) is required." >&2; exit 1; }

protect() {
  local branch="$1"
  echo "==> $REPO@$branch"

  # Build required_status_checks.contexts[] args from CHECKS.
  local args=()
  local check
  for check in "${CHECKS[@]}"; do
    args+=(-F "required_status_checks.contexts[]=$check")
  done

  if gh api -X PUT "repos/$REPO/branches/$branch/protection" \
      -F required_status_checks.strict=true \
      "${args[@]}" \
      -F enforce_admins=true \
      -F required_pull_request_reviews.required_approving_review_count=1 \
      -F required_pull_request_reviews.dismiss_stale_reviews=true \
      -F required_conversation_resolution=true \
      -F allow_force_pushes=false \
      -F allow_deletions=false \
      -F restrictions= >/dev/null 2>/tmp/bp_err.$$; then
    echo "    ✅ protected (checks: ${CHECKS[*]}; 1 approval; strict; no force-push)"
  else
    if grep -q "Upgrade to GitHub Pro" /tmp/bp_err.$$ 2>/dev/null; then
      cat >&2 <<MSG
    ❌ NOT APPLIED — the plan does not allow it.

       Protected branches need Pro/Team/Enterprise for PRIVATE repos; GitHub
       Free only supports them on public repos. This is the documented state in
       docs/markdown/BRANCH_PROTECTION.md, not a bug in this script.

       Until the org upgrades, enforcement does not exist: every CI gate is
       bypassable by clicking merge. The post-merge canary
       (.github/workflows/canary.yml) is the compensating control — it DETECTS a
       broken develop within ~12 minutes; it cannot prevent one.
MSG
    else
      echo "    ❌ failed:" >&2; sed 's/^/       /' /tmp/bp_err.$$ >&2
    fi
    rm -f /tmp/bp_err.$$
    return 1
  fi
  rm -f /tmp/bp_err.$$
}

rc=0
for b in "${branches[@]}"; do
  protect "$b" || rc=1
done

if [ "$rc" -eq 0 ]; then
  cat <<'MSG'

All requested branches protected. Follow-ups:
  1. canary.yml is now a redundant safety net (still useful as a post-merge smoke
     signal) — see its header before deciding to keep or delete it.
  2. Only NOW is it safe to consider paths-ignore on verify.yml: a skipped job
     reports "expected — waiting" against a REQUIRED check and blocks merges
     permanently.
MSG
fi
exit "$rc"
