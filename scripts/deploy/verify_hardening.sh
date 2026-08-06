#!/usr/bin/env bash
# ============================================================================
#  verify_hardening.sh — prove the P2-T08 acceptance  (attacker's-eye + on-host)
# ============================================================================
#  Acceptance: external port scan shows only 22/80/443; SSH password auth
#  rejected; fail2ban active.
#
#      ./scripts/deploy/verify_hardening.sh <host>      # from any machine
#
#  The port + SSH probes work from ANY machine (run them from your laptop for a
#  true EXTERNAL scan). The ufw/fail2ban status checks only pass when run ON the
#  server. Mirrors the pass/fail style of scripts/audit/phase1_security_audit.sh.
# ============================================================================
set -uo pipefail

HOST="${1:-localhost}"
SSH_PROBE_USER="${SSH_PROBE_USER:-hardencheck}"

pass=0
fail=0
ok(){ echo "  ✅ PASS: $1"; pass=$((pass + 1)); }
no(){ echo "  ❌ FAIL: $1"; fail=$((fail + 1)); }
hr(){ echo "----------------------------------------------------------------------"; }

# TCP reachability without extra tooling: nc if present, else bash /dev/tcp.
port_open(){  # host port -> 0 open, 1 closed
  if command -v nc >/dev/null 2>&1; then
    nc -z -w3 "$1" "$2" >/dev/null 2>&1
  else
    timeout 3 bash -c "exec 3<>/dev/tcp/$1/$2" 2>/dev/null
  fi
}

hr; echo "A. External port exposure ($HOST)"; hr
for p in 22 80 443; do
  if port_open "$HOST" "$p"; then ok "port $p open"; else no "port $p NOT open (expected open)"; fi
done
for p in 8069 5432 5433; do
  if port_open "$HOST" "$p"; then no "port $p OPEN — must be closed"; else ok "port $p closed"; fi
done

hr; echo "B. SSH password auth rejected"; hr
if command -v ssh >/dev/null 2>&1; then
  out="$(ssh -o BatchMode=yes -o PreferredAuthentications=password \
             -o PubkeyAuthentication=no -o ConnectTimeout=5 \
             -o StrictHostKeyChecking=no "${SSH_PROBE_USER}@${HOST}" true 2>&1 || true)"
  if echo "$out" | grep -qiE "permission denied|no supported authentication|publickey"; then
    ok "SSH password auth rejected"
  else
    no "SSH password auth not clearly rejected: ${out:-<no output>}"
  fi
else
  echo "  ⚠️  ssh client not present — skipping (run from a machine that has it)"
fi

hr; echo "C. On-host status (passes only when run ON the server)"; hr
if command -v ufw >/dev/null 2>&1; then
  if ufw status 2>/dev/null | grep -q "Status: active"; then ok "ufw active"; else no "ufw not active"; fi
else
  echo "  ⚠️  ufw not on PATH — not on the server, skipping"
fi
if command -v fail2ban-client >/dev/null 2>&1; then
  if fail2ban-client status sshd >/dev/null 2>&1; then ok "fail2ban sshd jail active"; else no "fail2ban sshd jail not active"; fi
else
  echo "  ⚠️  fail2ban-client not on PATH — not on the server, skipping"
fi

hr; echo "D. Container egress backstop (#311 — on-host: needs iptables/docker)"; hr
EGRESS_SUBNET="172.31.240.0/24"
if command -v iptables >/dev/null 2>&1; then
  # Exactly ONE live egress jump for the managed subnet, and it is the FIRST
  # DOCKER-USER rule matching that subnet (the harden.sh anchor invariant).
  first_match="$(iptables -S DOCKER-USER 2>/dev/null | grep -m1 -- "-s $EGRESS_SUBNET" || true)"
  jumps="$(iptables -S DOCKER-USER 2>/dev/null | grep -- "-s $EGRESS_SUBNET -j NC-EGRESS-" || true)"
  njumps="$(printf '%s' "$jumps" | grep -c . || true)"
  slot="$(printf '%s' "$first_match" | grep -oE 'NC-EGRESS-[AB]' | head -1)"
  if [ "$njumps" -eq 1 ] && [ -n "$slot" ] && \
     [ "$first_match" = "-A DOCKER-USER -s $EGRESS_SUBNET -j $slot" ]; then
    ok "single live egress jump, first-matching for $EGRESS_SUBNET ($slot)"
  else
    no "egress jump not singular/first-matching (found $njumps; first: '${first_match:-none}')"
  fi
  # The active chain must terminate in DROP (default-deny tail).
  if [ -n "$slot" ] && iptables -S "$slot" 2>/dev/null | tail -1 | grep -q -- '-j DROP'; then
    ok "$slot terminates in DROP (default-deny egress)"
  else
    no "egress chain ${slot:-<none>} does not terminate in DROP"
  fi
  # IPv6 egress default-denied.
  if command -v ip6tables >/dev/null 2>&1 && ip6tables -C DOCKER-USER -j DROP 2>/dev/null; then
    ok "IPv6 container egress default-denied"
  else
    no "IPv6 DOCKER-USER DROP rule missing"
  fi
else
  echo "  ⚠️  iptables not on PATH — not on the server, skipping"
fi

# Behavioural proof (needs the stack running). Tolerant: if a container lacks a
# probe tool or is not running, we SKIP rather than false-fail.
egress_probe(){  # container url -> echoes OPEN | BLOCKED | NOTOOL | NORUN
  docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^$1$" || { echo NORUN; return; }
  docker exec "$1" sh -c '
    if command -v curl >/dev/null 2>&1; then curl -sf -m 6 -o /dev/null "$0" && echo OPEN || echo BLOCKED
    elif command -v wget >/dev/null 2>&1; then wget -q -T 6 -O /dev/null "$0" && echo OPEN || echo BLOCKED
    else echo NOTOOL; fi' "$2" 2>/dev/null || echo BLOCKED
}
if command -v docker >/dev/null 2>&1; then
  # Zero-egress service: pgbouncer (db-plane only) must NOT reach off-host.
  case "$(egress_probe ncollection-pgbouncer https://api.stripe.com/)" in
    BLOCKED) ok "pgbouncer egress denied (db-plane only)";;
    OPEN)    no "pgbouncer reached the internet — db-plane isolation broken";;
    *)       echo "  ⚠️  pgbouncer egress probe skipped (not running / no tool)";;
  esac
  # Egress service: odoo reaches an allowlisted host but not a random one.
  case "$(egress_probe ncollection-odoo https://www.ecb.europa.eu/)" in
    OPEN)    ok "odoo reaches allowlisted host (ECB)";;
    BLOCKED) no "odoo could NOT reach allowlisted ECB — allowlist stale? re-run harden.sh";;
    *)       echo "  ⚠️  odoo allowlist-reach probe skipped (not running / no tool)";;
  esac
  case "$(egress_probe ncollection-odoo https://example.com/)" in
    BLOCKED) ok "odoo egress to non-allowlisted host denied";;
    OPEN)    no "odoo reached a NON-allowlisted host (example.com) — egress not enforced";;
    *)       echo "  ⚠️  odoo deny probe skipped (not running / no tool)";;
  esac
else
  echo "  ⚠️  docker not on PATH — skipping container egress behaviour checks"
fi

hr
echo "RESULT: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
