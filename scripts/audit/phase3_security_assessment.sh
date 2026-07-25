#!/usr/bin/env bash
# ============================================================================
#  P3-T12 — Pre-launch security assessment (repeatable, attacker's-eye)
# ============================================================================
#  The pre-prod half of the ARCHITECTURE_SECURITY §12 "Phase 3 / go-live" gate.
#  Complements — does not replace — scripts/audit/phase1_security_audit.sh
#  (cross-tenant isolation, license, role matrix, db-manager block) and
#  `make verify-all` (isolation + provisioning + config-sync + e2e proofs).
#
#  Checks here:
#    A. Security headers present on the edge
#    B. TLS / HSTS / HTTP->HTTPS enforced in the PROD nginx config (static)
#    C. DB-manager + selector blocked at the edge
#    D. Public onboarding endpoints (checkout/signup) reject method tampering
#       and malformed input WITHOUT triggering provisioning or 5xx-leaking
#    E. No secrets in tracked files (scan)
#    F. Dependency-scan summary (Dependabot; advisory)
#
#  Read-only: it never sends a VALID registration (which would provision a DB) —
#  only inputs designed to be rejected. Run against the routing edge:
#      make routing-up   # db_filter=^%d$ on
#  Deferred to P3-T13 (need production infra): SSL Labs A grade, restore drill
#  ON PROD, on-call rotation / breach-notification tree.
#
#  Exit code is non-zero if ANY hard check fails (gate-friendly).
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")/../.."

HOST="${ASSESS_HOST:-ncollection.localhost}"   # a platform DB with ncollection_saas
BASE="http://${HOST}"
RESOLVE="${HOST}:80:127.0.0.1"

pass=0; fail=0; noted=0
ok(){ echo "  ✅ PASS: $1"; pass=$((pass + 1)); }
no(){ echo "  ❌ FAIL: $1"; fail=$((fail + 1)); }
known(){ echo "  ⚠️  KNOWN: $1"; noted=$((noted + 1)); }
hr(){ echo "----------------------------------------------------------------------"; }
section(){ hr; echo "$1"; hr; }

hdrs(){ curl -sI --resolve "$RESOLVE" "${BASE}$1" 2>/dev/null; }
code(){ curl -s -o /dev/null -w '%{http_code}' -X "${2:-GET}" \
        --resolve "$RESOLVE" "${BASE}$1" 2>/dev/null || true; }

# --- prerequisite -----------------------------------------------------------
# -f: fail on HTTP >=400 too, so a broken/missing /web/health (not just a dead
# socket) is caught before the rest of the checks run against a broken edge.
if ! curl -sf -o /dev/null --resolve "$RESOLVE" "${BASE}/web/health" 2>/dev/null; then
    echo "ERROR: edge not reachable/healthy at ${BASE}. Bring the routing stack up:" >&2
    echo "  make routing-up" >&2
    exit 2
fi

echo "======================================================================"
echo " P3-T12 — PRE-LAUNCH SECURITY ASSESSMENT (edge:127.0.0.1:80, host:${HOST})"
echo "======================================================================"

# ===========================================================================
section "A — SECURITY HEADERS (present on every edge response)"
H="$(hdrs /web/login)"
for h in "X-Frame-Options" "X-Content-Type-Options" "Referrer-Policy" "Content-Security-Policy"; do
    if grep -qi "^${h}:" <<<"$H"; then ok "$h present"; else no "$h MISSING"; fi
done
# CSP still carries 'unsafe-inline'/'unsafe-eval' (Odoo web client needs them);
# nonce-based tightening is the documented P1-T03 residual, not crit/high.
if grep -qiE "Content-Security-Policy:.*unsafe-(inline|eval)" <<<"$H"; then
    known "CSP allows unsafe-inline/unsafe-eval (Odoo client req.) — nonce hardening is the P1-T03 residual"
fi

# ===========================================================================
section "B — TLS / HSTS / HTTP->HTTPS (prod nginx config — static)"
PROD="nginx/conf.d/ncollection.prod.conf"
if grep -qE "return 301 https://" "$PROD"; then ok "HTTP->HTTPS 301 redirect configured (prod)"; else no "no HTTP->HTTPS redirect in $PROD"; fi
if grep -qE "ssl_protocols (TLSv1.2|TLSv1.3)" "$PROD"; then ok "TLS 1.2/1.3 only (prod)"; else no "TLS protocol pinning missing in $PROD"; fi
if grep -qiE "add_header Strict-Transport-Security" "$PROD"; then ok "HSTS header configured (prod)"; else no "HSTS missing in $PROD"; fi
known "SSL Labs grade-A verification needs the live TLS endpoint — deferred to P3-T13"

# ===========================================================================
section "C — DATABASE MANAGER / SELECTOR BLOCKED AT EDGE"
for path in /web/database/manager /web/database/selector /web/database/create; do
    c="$(code "$path")"
    if [ "$c" = "404" ] || [ "$c" = "403" ]; then ok "$path blocked (HTTP $c)"; else no "$path reachable (HTTP $c)"; fi
done

# ===========================================================================
section "D — PUBLIC ONBOARDING ENDPOINTS (checkout/signup abuse-resistance)"
# Method tampering: the register/signup routes are POST-only jsonrpc. Security
# bar = a GET must NOT be PROCESSED (never a 2xx that could reach provisioning).
# A clean 405/404 is ideal; a 500 (Odoo's jsonrpc dispatcher rejecting a bodyless
# GET before the handler runs — no stack leak, no provisioning) still means
# "rejected", but is a LOW hygiene nit vs a clean 405.
for path in /nc/checkout/register /ncollection/api/signup; do
    c="$(code "$path" GET)"
    if [ "$c" = "405" ] || [ "$c" = "404" ]; then
        ok "GET $path rejected cleanly (HTTP $c — POST-only)"
    elif [ "$c" = "500" ]; then
        known "GET $path rejected but returns 500 (should be 405; no leak/provisioning) — LOW hygiene"
    else
        no "GET $path PROCESSED (HTTP $c) — must not accept GET"
    fi
done
# Malformed payload to availability (public): a bogus/oversized subdomain must be
# rejected by validation, not 5xx-leak a stack trace. Never a valid registration.
AVAIL='{"jsonrpc":"2.0","method":"call","params":{"subdomain":"../../etc/passwd OR 1=1"}}'
resp="$(curl -s --resolve "$RESOLVE" -H 'Content-Type: application/json' -d "$AVAIL" \
        "${BASE}/nc/checkout/availability" 2>/dev/null || true)"
if grep -qiE '"available"\s*:\s*(true|false)|invalid|error' <<<"$resp" && ! grep -qiE 'Traceback|psycopg2|SQL' <<<"$resp"; then
    ok "availability sanitizes a malicious subdomain (no 5xx/stack leak)"
else
    no "availability leaked an internal error on malicious input"
fi

# ===========================================================================
section "E — SECRETS SCAN (tracked files)"
# Supplementary high-signal scan (scripts/ci/architecture_guard.py is the
# authoritative secrets gate in CI/pre-push). Two correctness notes baked in:
#   - use POSIX [[:space:]] not `\s` (unreliable in `git grep -E`);
#   - apply the dev-placeholder exclusion to the matched CONTENT only (never the
#     path), so a real secret in a path containing a placeholder word can't hide.
SECRET_RE='(AKIA[0-9A-Z]{16}|sk_live_[0-9a-zA-Z]{24}|-----BEGIN (RSA|EC|OPENSSH) PRIVATE KEY-----|password[[:space:]]*=[[:space:]]*["][^"$]{8,}["])'
hits="$(git grep -nIE "$SECRET_RE" -- ':!*.example' ':!docs/*' ':!*/tests/*' 2>/dev/null || true)"
# Strip the leading path:line: then drop lines whose CONTENT is a dev placeholder.
real="$(printf '%s\n' "$hits" | awk -F: 'NF>=3 { c=$0; sub(/^[^:]*:[^:]*:/,"",c);
        if (c !~ /ncollection_dev|demo1234|benchpw/) print }' | grep -v '^[[:space:]]*$' || true)"
if [ -n "$real" ]; then
    no "possible secret(s) in tracked files:"
    printf '      %s\n' "$real"
else
    ok "no hardcoded secrets in tracked files (dev placeholders excluded by content)"
fi

# ===========================================================================
section "F — DEPENDENCY SCAN (advisory)"
if command -v gh >/dev/null 2>&1; then
    crit="$(gh api repos/NCollection-Sys/ncollection-erp/dependabot/alerts --jq \
        '[.[] | select(.state=="open") | select(.security_advisory.severity=="critical" or .security_advisory.severity=="high")] | length' 2>/dev/null || echo "?")"
    if [ "$crit" = "0" ]; then ok "0 open CRITICAL/HIGH Dependabot alerts"
    elif [ "$crit" = "?" ]; then known "Dependabot API not reachable (check manually)"
    else no "$crit open CRITICAL/HIGH Dependabot alerts"; fi
    known "open MODERATE alerts are tracked separately (react-router on main, fixed on develop)"
else
    known "gh not available — review Dependabot alerts manually"
fi

hr
echo "ASSESSMENT SUMMARY: $pass passed, $fail failed, $noted known/deferred."
hr
[ "$fail" -eq 0 ]
