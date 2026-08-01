#!/usr/bin/env bash
# ============================================================================
#  go_live_check.sh — the P3-T13 go-live readiness preflight
# ============================================================================
#  Acceptance (P3-T13): production serves a real paying tenant; every checklist
#  item has linked evidence. See docs/markdown/GO_LIVE_CHECKLIST.md.
#
#      make go-live-check              # or: ./scripts/deploy/go_live_check.sh
#
#  This gate has two halves (same split as RUNBOOK_STAGING.md P2-T07):
#
#    AUTOMATED  — the go-live *instrument* is in place (deploy pipeline, rollback,
#                 hardening, monitoring/backup/incident runbooks, regression
#                 suite, secret hygiene). This script VERIFIES these and its exit
#                 code reflects them. Read-only — it never deploys or mutates.
#    MANUAL     — real-world steps only a human operator can perform on real paid
#                 infrastructure (deploy prod, onboard a paying tenant, verify
#                 PITR ON PRODUCTION, agree on-call). This script LISTS them as
#                 reminders; it cannot and does not confirm them.
#
#  So a green run means "everything automatable for go-live is ready" — NOT
#  "we are live". The issue (#53) closes only when the MANUAL items are done and
#  their evidence is linked in the checklist. Idempotent: run twice, same result.
#  Mirrors the pass/fail style of scripts/deploy/verify_hardening.sh.
# ============================================================================
set -uo pipefail

# Resolve repo root from this script's location (works from any CWD).
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT" || { echo "cannot cd to repo root $ROOT" >&2; exit 1; }

pass=0
fail=0
ok(){   echo "  ✅ PASS: $1"; pass=$((pass + 1)); }
no(){   echo "  ❌ FAIL: $1"; fail=$((fail + 1)); }
todo(){ echo "  ⏳ MANUAL (operator must confirm on production): $1"; }
hr(){ echo "----------------------------------------------------------------------"; }

# A file exists and is non-empty.
have_file(){ [ -s "$1" ]; }
# A script exists and is executable.
have_exec(){ [ -x "$1" ]; }
# A Makefile target is defined.
have_target(){ grep -qE "^$1:" Makefile 2>/dev/null; }

hr; echo "A. Deploy pipeline (ships in this repo)"; hr
for f in Dockerfile docker-compose.staging.yml .github/workflows/deploy-staging.yml; do
  if have_file "$f"; then ok "$f present"; else no "$f MISSING"; fi
done
for s in scripts/deploy/deploy.sh scripts/deploy/rollback.sh \
         scripts/deploy/smoke-test.sh scripts/deploy/harden.sh; do
  if have_exec "$s"; then ok "$s present + executable"; else no "$s MISSING or not executable"; fi
done

hr; echo "B. Rollback rehearsable (checklist: 'rollback procedure rehearsed')"; hr
if have_exec scripts/deploy/rollback.sh; then
  ok "rollback.sh runnable"
  if command -v shellcheck >/dev/null 2>&1; then
    if shellcheck -S error scripts/deploy/rollback.sh >/dev/null 2>&1; then
      ok "rollback.sh passes shellcheck (no errors)"
    else
      no "rollback.sh has shellcheck errors — fix before rehearsing"
    fi
  fi
else
  no "rollback.sh missing — cannot rehearse rollback"
fi

hr; echo "C. Host hardening (P2-T08)"; hr
if have_exec scripts/deploy/verify_hardening.sh; then ok "verify_hardening.sh present"; else no "verify_hardening.sh MISSING"; fi
for c in sshd_ncollection.conf fail2ban-sshd.local docker-daemon.json \
         apt-unattended-upgrades.conf; do
  if have_file "config/hardening/$c"; then ok "config/hardening/$c present"; else no "config/hardening/$c MISSING"; fi
done

hr; echo "D. Operational runbooks (evidence the gate links to)"; hr
declare -a RUNBOOKS=(
  "RUNBOOK_MONITORING.md:monitoring + alerting (P2-T10)"
  "RUNBOOK_BACKUP_PITR.md:PITR (P2-T04)"
  "RUNBOOK_TENANT_BACKUP.md:tenant backups (P2-T05)"
  "RUNBOOK_SECURITY.md:security hardening (P2-T08)"
  "RUNBOOK_INCIDENTS.md:incident response + on-call (P3-T13)"
)
for entry in "${RUNBOOKS[@]}"; do
  file="docs/markdown/${entry%%:*}"; label="${entry#*:}"
  if have_file "$file"; then ok "$label → ${entry%%:*}"; else no "$label → ${entry%%:*} MISSING"; fi
done

hr; echo "E. Regression + secret hygiene"; hr
if have_target verify-all; then ok "'make verify-all' target defined (full regression + E2E)"; else no "'make verify-all' target MISSING"; fi
if have_file docs/markdown/PHASE1_REGRESSION_CHECKLIST.md; then ok "regression checklist present"; else no "regression checklist MISSING"; fi
if have_file docs/markdown/GO_LIVE_CHECKLIST.md; then ok "go-live checklist present"; else no "GO_LIVE_CHECKLIST.md MISSING"; fi
if have_file .env.example; then ok ".env.example present"; else no ".env.example MISSING"; fi
if git check-ignore .env >/dev/null 2>&1; then ok ".env is gitignored (no secrets in git)"; else no ".env NOT gitignored"; fi

hr; echo "F. MANUAL go-live steps — real infrastructure, operator-only"; hr
todo "Deploy the production server + run scripts/deploy/deploy.sh (first prod deploy)"
todo "Verify PITR + tenant backups ON PRODUCTION (restore-test, not just config)"
todo "Confirm monitoring + alerting fire on the production host"
todo "Sign off the security assessment (P3-T12) against production"
todo "Rehearse rollback.sh on staging/production and record the outcome"
todo "Agree the on-call rotation with real people (RUNBOOK_INCIDENTS.md)"
todo "Onboard the FIRST real paying tenant and link its evidence in the checklist"

hr
echo "AUTOMATED readiness: $pass passed, $fail failed."
echo "MANUAL steps above are NOT auto-verifiable — confirm each on production and"
echo "link evidence in docs/markdown/GO_LIVE_CHECKLIST.md before closing #53."
hr
if [ "$fail" -gt 0 ]; then
  echo "❌ NOT go-live ready — $fail automated precondition(s) failing."
  exit 1
fi
echo "✅ Automated go-live preconditions READY. Complete the MANUAL steps to go live."
exit 0
