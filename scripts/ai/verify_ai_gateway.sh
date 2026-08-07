#!/usr/bin/env bash
# shellcheck disable=SC2015
# ============================================================================
#  #59 / P5-T02 — AI gateway satellite proof (local)
# ============================================================================
#  Proves the properties the SATELLITE TOPOLOGY exists for, against a running
#  container rather than by reading the compose file:
#
#    1. The gateway has NO ROUTE TO ANY DATABASE. This is the load-bearing
#       claim of ARCHITECTURE_DATA_PLATFORM §10.4 ("LLM keys but no DB
#       credentials — it receives context, never fetches it") and of
#       ARCHITECTURE_SECURITY §11 ("no database, tenant or admin, is in the
#       egress path"). Omitting credentials would rely on configuration staying
#       correct forever; the service is off `nc_dbplane` entirely, so the
#       property is structural. This asserts the structure holds.
#    2. It answers /healthz (the §10 satellite contract).
#    3. It completes a request over the compose network, as Odoo will.
#    4. Budget exhaustion returns a FRIENDLY 429 — the ticket's acceptance
#       criterion — and not a 500.
#    5. One tenant cannot exhaust another's budget.
#
#  Owns no database and creates no fixtures: the gateway has no database, which
#  is the point. It brings its own container up and leaves it running.
#
#  Usage:  make ai-verify
# ============================================================================
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 1

DC=(docker compose -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.ai.yml)
pass=0; fail=0
ok(){ echo "  ✅ PASS: $1"; pass=$((pass + 1)); }
no(){ echo "  ❌ FAIL: $1"; fail=$((fail + 1)); }
hr(){ echo "----------------------------------------------------------------------"; }

# A tiny HTTP client run INSIDE odoo, so every call traverses the compose
# network exactly as the tenant module will. Prints "<status>|<body>".
call(){  # $1=method $2=path $3=json-body(optional)
  "${DC[@]}" exec -T odoo python3 -c "
import json, sys, urllib.request, urllib.error
method, path = sys.argv[1], sys.argv[2]
data = sys.argv[3].encode() if len(sys.argv) > 3 and sys.argv[3] else None
req = urllib.request.Request('http://ai-gateway:8080' + path, data=data,
      method=method, headers={'content-type': 'application/json'})
try:
    with urllib.request.urlopen(req, timeout=15) as r:
        print(str(r.status) + '|' + r.read().decode())
except urllib.error.HTTPError as e:
    print(str(e.code) + '|' + e.read().decode())
except Exception as e:
    print('0|' + str(e))
" "$1" "$2" "${3:-}" 2>/dev/null
}

echo "#59 — AI gateway satellite proof"
hr
echo "== 0) bring the satellite up (budget deliberately tiny, to exhaust it) =="
NC_AI_TOKEN_BUDGET=60 NC_AI_MAX_REQUESTS=100 \
  "${DC[@]}" up -d --force-recreate ai-gateway >/dev/null 2>&1
for _ in $(seq 1 20); do
  [ "$(call GET /healthz | cut -d'|' -f1)" = "200" ] && break
  sleep 1
done

hr
echo "== 1) THE SECURITY PROPERTY: no route to any database =="
db_probe="$("${DC[@]}" exec -T ai-gateway python3 -c "
import socket
s = socket.socket(); s.settimeout(4)
try:
    s.connect(('db', 5432)); print('REACHED')
except Exception as e:
    print('BLOCKED:' + type(e).__name__)
" 2>/dev/null | tr -d '[:space:]')"
case "$db_probe" in
  BLOCKED*) ok "gateway cannot reach db:5432 ($db_probe)" ;;
  REACHED)  no "gateway REACHED the database — §10.4 isolation is broken" ;;
  *)        no "db probe inconclusive ('$db_probe')" ;;
esac

hr
echo "== 2) /healthz (satellite contract, §10) =="
health="$(call GET /healthz)"
[ "$(printf '%s' "$health" | cut -d'|' -f1)" = "200" ] \
  && ok "/healthz answers 200" || no "/healthz did not answer 200 ($health)"
printf '%s' "$health" | grep -q '"provider"' \
  && ok "health reports the active provider" || no "health omits the provider"

hr
echo "== 3) a completion over the compose network =="
# max_tokens is set explicitly and small. The budget check RESERVES
# prompt + max_tokens before calling (a ceiling charged only on success is not a
# ceiling), so against this deliberately tiny 60-token budget the 1024 default
# would be refused before the provider is ever reached. That is correct
# behaviour, and the gateway now warns about it at startup — see the
# config_warning in gateway.py.
res="$(call POST /v1/complete '{"tenant":"probe-a","prompt":"hello","max_tokens":8}')"
[ "$(printf '%s' "$res" | cut -d'|' -f1)" = "200" ] \
  && ok "completion returned 200" || no "completion failed ($res)"
printf '%s' "$res" | grep -q '"total_tokens"' \
  && ok "usage is reported (budget accounting is real)" || no "no usage in response"

hr
echo "== 4) ACCEPTANCE: budget exhaustion is a FRIENDLY 429, not a 500 =="
# Budget is 60 tokens; a ~400-char prompt plus max_tokens blows through it.
call POST /v1/complete '{"tenant":"probe-b","prompt":"'"$(head -c 400 < /dev/zero | tr '\0' 'x')"'","max_tokens":40}' >/dev/null
exhausted="$(call POST /v1/complete '{"tenant":"probe-b","prompt":"'"$(head -c 400 < /dev/zero | tr '\0' 'x')"'","max_tokens":40}')"
code="$(printf '%s' "$exhausted" | cut -d'|' -f1)"
body="$(printf '%s' "$exhausted" | cut -d'|' -f2-)"
[ "$code" = "429" ] && ok "budget exhaustion returns 429 (not 500)" \
  || no "expected 429 on exhaustion, got $code"
printf '%s' "$body" | grep -q 'ai_budget_exhausted' \
  && ok "error is machine-identifiable (ai_budget_exhausted)" || no "no error code"
printf '%s' "$body" | grep -qi 'allowance' \
  && ok "message is human-friendly and actionable" || no "message is not friendly"
printf '%s' "$body" | grep -qi 'traceback' \
  && no "message leaks a traceback" || ok "no traceback leaked to the caller"

hr
echo "== 5) one tenant cannot exhaust another =="
other="$(call POST /v1/complete '{"tenant":"probe-c","prompt":"hi","max_tokens":5}')"
[ "$(printf '%s' "$other" | cut -d'|' -f1)" = "200" ] \
  && ok "a different tenant is unaffected by probe-b's exhaustion" \
  || no "cross-tenant budget bleed ($other)"

hr
echo "SUMMARY: $pass passed, $fail failed."
[ "$fail" -eq 0 ] || exit 1
echo "✅ The gateway is the choke point, and it cannot reach a database."
