#!/usr/bin/env bash
# shellcheck disable=SC2015  # the repo's A && ok || no reporting idiom
# ============================================================================
#  Self-test of verify_edge_domain.sh: its verdict must follow `nginx -t`'s own exit status (pre-commit review M4).
# ============================================================================
#  The check used to run `nginx -t | sed`, inside an `sh -e` without pipefail, so the pipeline's status was sed's: a
#  configuration nginx refused still printed "OK ... nginx -t passes" and exited 0. This runs the real check twice, in
#  the real nginx image:
#    * the production template                                  -> must exit 0
#    * the same template plus one directive nginx does not know -> must exit non-zero, and say nginx refused it
#  The broken copy passes every grep the check makes before `nginx -t`, so only nginx's own verdict can catch it.
#  Docker only; the broken copy lives in a temp directory and is removed on exit.
#
#  usage: scripts/deploy/test_verify_edge_domain.sh
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")/../.."
CHECK="scripts/deploy/verify_edge_domain.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
pass=0; fail=0
ok(){ echo "  ✅ PASS: $1"; pass=$((pass + 1)); }
no(){ echo "  ❌ FAIL: $1"; fail=$((fail + 1)); }

echo "== valid production template =="
rc=0; bash "$CHECK" balancecloud.ae >"$TMP/valid.log" 2>&1 || rc=$?
[ "$rc" -eq 0 ] && ok "the production template passes (exit 0)" || { no "the production template failed (exit $rc)"; cat "$TMP/valid.log"; }

echo "== template nginx refuses =="
cp nginx/conf.d/ncollection.prod.conf "$TMP/broken.conf"
printf '\nnc_not_a_directive on;\n' >> "$TMP/broken.conf"
BROKEN="$(cygpath -w "$TMP/broken.conf" 2>/dev/null || printf '%s' "$TMP/broken.conf")"
rc=0; EDGE_TEMPLATE="$BROKEN" bash "$CHECK" balancecloud.ae >"$TMP/broken.log" 2>&1 || rc=$?
[ "$rc" -ne 0 ] && ok "a configuration nginx refuses fails the check (exit $rc)" \
  || { no "a configuration nginx refuses PASSED the check"; cat "$TMP/broken.log"; }
grep -q "nginx -t refused the rendered configuration" "$TMP/broken.log" \
  && ok "it says why: nginx -t refused it" || no "it did not report nginx's refusal"
grep -q "unknown directive" "$TMP/broken.log" \
  && ok "nginx's own words are shown (unknown directive)" || no "nginx's output was not shown"
! grep -q "  OK: server_name" "$TMP/broken.log" \
  && ok "no OK line is printed for the refused configuration" || no "an OK line was printed over a refused configuration"

echo "SUMMARY: $pass passed, $fail failed."
[ "$fail" -eq 0 ]
