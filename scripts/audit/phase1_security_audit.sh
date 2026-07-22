#!/usr/bin/env bash
# ============================================================================
#  P1-T21 — Phase 1 security audit (repeatable, attacker's-eye)
# ============================================================================
#  The security half of the Phase 1 gate. Complements the functional suites
#  (make verify-all: routing + e2e + provisioning) with explicit ATTACK probes
#  that must be REJECTED, plus the full 8-role access matrix.
#
#  It proves, from the outside, the guarantees the platform is built on:
#    A. cross-tenant RPC isolation      — a session on tenant X is worthless on Y
#    B. license enforcement (Ring 2)    — an unlicensed model is denied by ORM,
#                                          not merely hidden (P1-T10)
#    C. the 8-role access matrix        — each role reaches exactly its surface
#    D. the DB manager is unreachable   — the edge blocks it (403)
#
#  Read-only against the tenants (it authenticates and probes; it never writes).
#  Uses /web/session/authenticate, which is NOT rate-limited (only the login
#  FORM at `location = /web/login` is), so it never trips the P1-T03 429 limiter.
#
#  Prerequisites — the audit environment MUST be on CURRENT code (run
#  `make doctor` first; a schema behind its code will crash auth). Bring it up:
#    make routing-up
#    make e2e-clean && bash e2e/scripts/setup_e2e_tenants.sh   # Pro / Basic
#    REBUILD=1 make demo-tenant                                # albarari, 8 roles
# ============================================================================
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 1   # repo root

DEMO_PW="${DEMO_ADMIN_PASSWORD:-demo1234}"
BIZ_PW="demo1234"

pass=0; fail=0; noted=0
ok(){ echo "  ✅ PASS: $1"; pass=$((pass + 1)); }
no(){ echo "  ❌ FAIL: $1"; fail=$((fail + 1)); }
# A documented, accepted gap (has a ticket). Reported, but does not fail the gate.
known(){ echo "  ⚠️  KNOWN: $1"; noted=$((noted + 1)); }
hr(){ echo "----------------------------------------------------------------------"; }
section(){ hr; echo "$1"; hr; }

# --- HTTP / RPC helpers (reach a subdomain via the edge, no /etc/hosts) ------
authenticate(){   # authenticate <sub> <db> <login> <pw> <cookiejar> ; echoes uid
  curl -s --resolve "${1}.localhost:80:127.0.0.1" -c "$5" \
    -H 'Content-Type: application/json' \
    -d "{\"jsonrpc\":\"2.0\",\"method\":\"call\",\"params\":{\"db\":\"${2}\",\"login\":\"${3}\",\"password\":\"${4}\"}}" \
    "http://${1}.localhost/web/session/authenticate" 2>/dev/null | j_uid
}
rpc(){   # rpc <sub> <cookiejar> <path> <json-body>
  curl -s --resolve "${1}.localhost:80:127.0.0.1" -b "$2" \
    -H 'Content-Type: application/json' -d "$4" \
    "http://${1}.localhost${3}" 2>/dev/null || true
}
http_code(){   # http_code <sub> <path>
  curl -s -o /dev/null -w '%{http_code}' \
    --resolve "${1}.localhost:80:127.0.0.1" "http://${1}.localhost${2}" 2>/dev/null || true
}
call_kw(){   # call_kw <sub> <jar> <model> <method> <args-json>
  rpc "$1" "$2" /web/dataset/call_kw \
    "{\"jsonrpc\":\"2.0\",\"method\":\"call\",\"params\":{\"model\":\"$3\",\"method\":\"$4\",\"args\":$5,\"kwargs\":{}}}"
}
readable(){   # readable <sub> <jar> <model> ; echoes yes/no (no AccessError on read)
  local e; e="$(call_kw "$1" "$2" "$3" search_count '[[]]' | j_has_error)"
  [ "$e" = "no" ] && echo yes || echo no
}

# --- JSON extractors (stdin, tolerant) --------------------------------------
j_uid(){ python3 -c 'import sys,json
try: r=json.load(sys.stdin).get("result") or {}
except Exception: r={}
print(r.get("uid") if isinstance(r,dict) and r.get("uid") else "")'; }
j_has_error(){ python3 -c 'import sys,json
try: d=json.load(sys.stdin)
except Exception: d={}
print("yes" if d.get("error") else "no")'; }
j_menu_has(){ python3 -c 'import sys,json
name=sys.argv[1]
try: menus=json.load(sys.stdin).get("result") or {}
except Exception: menus={}
vals=menus.values() if isinstance(menus,dict) else []
print("yes" if any(isinstance(m,dict) and m.get("name")==name for m in vals) else "no")' "$1"; }
menu_visible(){   # menu_visible <sub> <jar> <name> ; echoes yes/no
  call_kw "$1" "$2" ir.ui.menu load_menus '[false]' | j_menu_has "$3"
}

SESSION_BODY='{"jsonrpc":"2.0","method":"call","params":{}}'
TMPD="$(mktemp -d)"; JAR="$TMPD/jar"; trap 'rm -rf "$TMPD"' EXIT

# --- prerequisites ----------------------------------------------------------
if ! curl -s -o /dev/null --resolve "e2eclienta.localhost:80:127.0.0.1" \
     http://e2eclienta.localhost/web/health 2>/dev/null; then
  echo "ERROR: edge not reachable. Bring the audit environment up first:" >&2
  echo "  make routing-up && make e2e-clean && bash e2e/scripts/setup_e2e_tenants.sh && REBUILD=1 make demo-tenant" >&2
  exit 2
fi

echo "======================================================================"
echo " P1-T21 — Phase 1 SECURITY AUDIT (edge:127.0.0.1:80)"
echo "======================================================================"

# ===========================================================================
section "A — CROSS-TENANT RPC ISOLATION (attacks MUST be rejected)"
# A tenant's session cookie forced onto another tenant must grant nothing.
ja="$JAR.a"; jb="$JAR.b"
ua="$(authenticate e2eclienta e2eclienta biz "$BIZ_PW" "$ja")"
ub="$(authenticate e2eclientb e2eclientb biz "$BIZ_PW" "$jb")"
if [ -z "$ua" ] || [ -z "$ub" ]; then
  no "PRECONDITION: biz could not authenticate (ua='$ua' ub='$ub') — is the environment current? run make doctor"
else
  u_ab="$(rpc e2eclientb "$ja" /web/session/get_session_info "$SESSION_BODY" | j_uid)"
  u_ba="$(rpc e2eclienta "$jb" /web/session/get_session_info "$SESSION_BODY" | j_uid)"
  if [ -z "$u_ab" ]; then ok "e2eclienta session is rejected on e2eclientb"; else no "e2eclienta session LEAKED to e2eclientb (uid=$u_ab) — SEV1"; fi
  if [ -z "$u_ba" ]; then ok "e2eclientb session is rejected on e2eclienta"; else no "e2eclientb session LEAKED to e2eclienta (uid=$u_ba) — SEV1"; fi
fi

# ===========================================================================
section "B — LICENSE ENFORCEMENT (Ring 2 — bypass attempts MUST fail)"
# e2eclientb is Basic (sale INSTALLED but UNLICENSED); e2eclienta is Pro.
# biz has the Sales groups, so any denial is the PLAN gate, not plain ACL.
authenticate e2eclientb e2eclientb biz "$BIZ_PW" "$jb" >/dev/null
authenticate e2eclienta e2eclienta biz "$BIZ_PW" "$ja" >/dev/null
if [ "$(readable e2eclientb "$jb" sale.order)" = "no" ]; then
  ok "RPC read of sale.order on Basic tenant is DENIED (Ring 2, P1-T10)"
else
  no "RPC read of sale.order on Basic tenant was ALLOWED — license bypass"
fi
code_b="$(http_code e2eclientb /odoo/action-sale.action_orders)"
if [ "$code_b" = "303" ] || [ "$code_b" = "200" ]; then
  ok "Sales action URL on Basic tenant returns $code_b (data still ORM-gated above)"
else
  no "Sales action URL on Basic tenant returned unexpected $code_b"
fi
# Ring 1 (menu hiding) is UX, not the security boundary. It correctly hides the
# unlicensed Sales menu from a normal user, but a user who HOLDS the sale group
# (like biz) still sees the menu root — the known F8 gap. Ring 2 (the check
# above) blocks the DATA regardless, so this is a UX inconsistency, not a breach.
if [ "$(menu_visible e2eclientb "$jb" Sales)" = "no" ]; then
  ok "Sales menu is HIDDEN on Basic tenant for biz (Ring 1, P1-T09)"
else
  known "Sales menu VISIBLE on Basic tenant to a sale-group holder — F8 (Ring 1 UX gap; Ring 2 blocks the data, proven above). Ticketed."
fi
if [ "$(readable e2eclienta "$ja" sale.order)" = "yes" ]; then
  ok "control: RPC read of sale.order on Pro tenant is ALLOWED"
else
  no "control: Pro tenant DENIED sale.order — enforcement over-broad or env not current"
fi

# ===========================================================================
section "C — 8-ROLE ACCESS MATRIX (albarari — all roles seeded)"
# login -> role, then the decisive probes against the REAL Odoo ACLs on this
# tenant (crm+sale+account installed; no stock/hr). The security guarantees the
# matrix proves: (1) ONLY the Owner reaches Settings (P1-T11, base.group_system);
# (2) roles with no operational groups — warehouse/hr/employee — see NOTHING
# financial or sales. The "business" roles legitimately read both models via
# STANDARD Odoo cross-app ACLs, verified live, not an over-permission:
#   - sales/manager read account.move.line via sales_team salesman ACL
#     ("Own Documents Only"), and read sale.order as sales users;
#   - accountant reads sale.order (accounting sees orders for invoicing) and
#     account.move.line as an account user.
#   role       account.move.line  sale.order  Settings menu
#   owner            yes              yes          yes
#   ceo              yes              yes          no
#   manager          yes              yes          no
#   sales            yes              yes          no
#   warehouse        no               no           no
#   hr               no               no           no
#   accountant       yes              yes          no
#   employee         no               no           no
probe_role(){   # probe_role <login> <role> <exp_acct> <exp_sale> <exp_settings>
  local login="$1" role="$2" ea="$3" es="$4" eset="$5" j="$JAR.$2" uid
  uid="$(authenticate albarari albarari "$login" "$DEMO_PW" "$j")"
  if [ -z "$uid" ]; then no "$role ($login) could not authenticate — is albarari current? run make doctor"; return; fi
  local acct sale settings
  acct="$(readable albarari "$j" account.move.line)"
  sale="$(readable albarari "$j" sale.order)"
  settings="$(menu_visible albarari "$j" Settings)"
  if [ "$acct" = "$ea" ] && [ "$sale" = "$es" ] && [ "$settings" = "$eset" ]; then
    ok "$role: account=$acct sale=$sale settings=$settings (as expected)"
  else
    no "$role: account=$acct/exp=$ea  sale=$sale/exp=$es  settings=$settings/exp=$eset"
  fi
}
probe_role owner@albarari.ae   owner      yes yes yes
probe_role omar@albarari.ae    ceo        yes yes no
probe_role sara@albarari.ae    manager    yes yes no
probe_role yousef@albarari.ae  sales      yes yes no
probe_role bilal@albarari.ae   warehouse  no  no  no
probe_role noura@albarari.ae   hr         no  no  no
probe_role fatima@albarari.ae  accountant yes yes no
probe_role aisha@albarari.ae   employee   no  no  no

# ===========================================================================
section "D — DATABASE MANAGER UNREACHABLE (edge block)"
for path in /web/database/manager /web/database/selector /web/database/list; do
  code="$(http_code e2eclienta "$path")"
  if [ "$code" = "403" ]; then ok "e2eclienta$path -> 403"; else no "e2eclienta$path -> $code (expected 403)"; fi
done

# ===========================================================================
hr
echo "SECURITY AUDIT SUMMARY: $pass passed, $fail failed, $noted known (ticketed)."
if [ "$fail" -eq 0 ]; then
  echo "✅ Phase 1 security posture holds (known/ticketed gaps noted above)."
  exit 0
fi
echo "❌ NEW security findings above — each must be fixed or ticketed before Phase 1 sign-off."
exit 1
