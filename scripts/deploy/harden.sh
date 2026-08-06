#!/usr/bin/env bash
# ============================================================================
#  harden.sh — production server hardening  (ticket P2-T08)
# ============================================================================
#  Idempotent: re-running converges to the same state. Run ONCE as root on a
#  fresh server, then again any time to re-assert. Applies the config templates
#  in config/hardening/ and enforces the firewall / SSH / fail2ban / auto-update
#  / Docker-daemon posture from ARCHITECTURE_SECURITY.md (Layer 5).
#
#      sudo NC_REPO_DIR=/opt/ncollection DEPLOY_USER=deploy ./scripts/deploy/harden.sh
#
#  Prove the result afterwards with scripts/deploy/verify_hardening.sh.
#  Full context + the 8069/nginx sequencing note: docs/markdown/RUNBOOK_SECURITY.md
# ============================================================================
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "!! Run as root (sudo)." >&2
  exit 1
fi

REPO_DIR="${NC_REPO_DIR:-/opt/ncollection}"
DEPLOY_USER="${DEPLOY_USER:-deploy}"
HARDEN_DIR="$REPO_DIR/config/hardening"

[ -d "$HARDEN_DIR" ] || { echo "!! $HARDEN_DIR not found (is NC_REPO_DIR correct?)." >&2; exit 1; }

# --- 0. SAFETY: never disable password auth without a working key ----------
# Locking out is the #1 hardening footgun — refuse if the deploy user has no key.
authkeys="/home/$DEPLOY_USER/.ssh/authorized_keys"
if [ ! -s "$authkeys" ]; then
  echo "!! $authkeys is missing/empty — refusing to disable password auth" >&2
  echo "   (that would lock you out). Install the deploy key first." >&2
  exit 1
fi

echo "==> [1/7] Firewall (UFW): allow 22/80/443, deny the rest"
# Allow SSH BEFORE enabling so an active session is never cut.
ufw --force reset >/dev/null
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

echo "==> [2/7] SSH: key-only, no root (validated before reload)"
install -m 0644 "$HARDEN_DIR/sshd_ncollection.conf" \
  /etc/ssh/sshd_config.d/60-ncollection.conf
sshd -t
systemctl reload ssh

echo "==> [3/7] fail2ban: ssh jail"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq fail2ban
install -m 0644 "$HARDEN_DIR/fail2ban-sshd.local" \
  /etc/fail2ban/jail.d/ncollection-sshd.local
systemctl enable --now fail2ban
systemctl restart fail2ban

echo "==> [4/7] Unattended security upgrades"
apt-get install -y -qq unattended-upgrades
install -m 0644 "$HARDEN_DIR/apt-auto-upgrades.conf" \
  /etc/apt/apt.conf.d/20auto-upgrades
install -m 0644 "$HARDEN_DIR/apt-unattended-upgrades.conf" \
  /etc/apt/apt.conf.d/52ncollection-unattended

echo "==> [5/7] Docker daemon hardening"
install -d -m 0755 /etc/docker
install -m 0644 "$HARDEN_DIR/docker-daemon.json" /etc/docker/daemon.json
# live-restore keeps tenants serving across this daemon restart.
systemctl restart docker

echo "==> [6/7] Secrets: .env 0600, owned by the deploy user"
if [ -f "$REPO_DIR/.env" ]; then
  chown "$DEPLOY_USER:$DEPLOY_USER" "$REPO_DIR/.env"
  chmod 600 "$REPO_DIR/.env"
fi

echo "==> [7/7] Container egress backstop (DOCKER-USER allowlist)"
# ---------------------------------------------------------------------------
# WHY: UFW's `default allow outgoing` does NOT cover containers — Docker wires
# its own FORWARD rules and consults the DOCKER-USER chain, bypassing UFW. So a
# compromised container can reach the whole internet even on a "hardened" host.
# This step default-DENIES egress from the managed egress subnet
# (172.31.240.0/24, see docker-compose.yml) and RETURNs (allows) only:
#   * already-ESTABLISHED/RELATED flows (replies to inbound),
#   * east-west traffic to our own two stack subnets, and
#   * each IP the allowlist hostnames currently resolve to.
# The internal-only db-plane (172.31.241.0/24) has no gateway, so it needs no
# rule of its own — Docker never forwards it off-host.
#
# LIFECYCLE (zero-gap A/B): the live policy is a chain NC-EGRESS-A or -B reached
# by a `-s <egress subnet> -j` jump at the TOP of DOCKER-USER. Each run builds
# the *inactive* slot fresh, inserts its jump at position 1, verifies it is the
# first matching rule, THEN removes the old jump/chain — so a DROP-terminated
# policy is present at every instant (never a flush-and-rebuild window).
# Idempotent: re-run any time to refresh resolved IPs (CDN drift) — it just
# ping-pongs to the other slot.
EGRESS_SUBNET="172.31.240.0/24"     # docker-compose.yml `default`  (egress-controlled)
DBPLANE_SUBNET="172.31.241.0/24"    # docker-compose.yml `nc_dbplane` (internal, east-west only)
ALLOWLIST_FILE="$HARDEN_DIR/egress_allowlist.txt"

[ -s "$ALLOWLIST_FILE" ] || { echo "!! $ALLOWLIST_FILE missing/empty — refusing to install an egress policy that would deny everything." >&2; exit 1; }

# Resolve every allowlisted host to IPv4 FIRST. Abort before touching iptables
# if any required host has no address — better a failed run than a live policy
# that silently blackholes ECB/Stripe/ACME/backups.
egress_ips=""
while read -r host; do
  [ -n "$host" ] || continue
  host_ips="$(getent ahostsv4 "$host" | awk '{print $1}' | sort -u)"
  if [ -z "$host_ips" ]; then
    echo "!! egress allowlist: '$host' resolved to no IPv4 address — aborting (not installing a policy that would break it)." >&2
    exit 1
  fi
  egress_ips+="$host_ips"$'\n'
done < <(grep -vE '^[[:space:]]*(#|$)' "$ALLOWLIST_FILE" | awk '{print $1}')
# numeric-sorted unique IPv4 set (deterministic chain ordering)
egress_ips="$(printf '%s' "$egress_ips" | grep -vE '^$' | sort -t. -k1,1n -k2,2n -k3,3n -k4,4n -u)"

# DOCKER-USER is normally created by dockerd; create it if docker hasn't yet so
# our rule survives and takes effect the moment docker wires its jump.
iptables -N DOCKER-USER 2>/dev/null || true

# Which slot is live? Pick the OTHER one to build into.
if iptables -S DOCKER-USER 2>/dev/null | grep -q -- '-j NC-EGRESS-B'; then
  active="B"
elif iptables -S DOCKER-USER 2>/dev/null | grep -q -- '-j NC-EGRESS-A'; then
  active="A"
else
  active=""
fi
case "$active" in A) target="B";; B) target="A";; *) target="A";; esac

# Build the inactive slot fresh.
iptables -N "NC-EGRESS-$target" 2>/dev/null || iptables -F "NC-EGRESS-$target"
iptables -A "NC-EGRESS-$target" -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
iptables -A "NC-EGRESS-$target" -d "$EGRESS_SUBNET"  -j RETURN   # east-west (same plane)
iptables -A "NC-EGRESS-$target" -d "$DBPLANE_SUBNET" -j RETURN   # east-west (db-plane)
while read -r ip; do
  [ -n "$ip" ] && iptables -A "NC-EGRESS-$target" -d "$ip/32" -j RETURN
done <<< "$egress_ips"
iptables -A "NC-EGRESS-$target" -j DROP

# Remove any stale jump to the target slot (interrupted prior run), then insert
# the new jump at position 1 — BEFORE deleting the old one (zero-gap).
while iptables -C DOCKER-USER -s "$EGRESS_SUBNET" -j "NC-EGRESS-$target" 2>/dev/null; do
  iptables -D DOCKER-USER -s "$EGRESS_SUBNET" -j "NC-EGRESS-$target"
done
iptables -I DOCKER-USER 1 -s "$EGRESS_SUBNET" -j "NC-EGRESS-$target"

# ANCHOR CHECK (per review): confirm OUR jump is the first DOCKER-USER rule that
# matches the managed egress subnet. If something sits above it, back out and
# fail rather than run with a bypassable policy.
first_match="$(iptables -S DOCKER-USER | grep -m1 -- "-s $EGRESS_SUBNET" || true)"
if [ "$first_match" != "-A DOCKER-USER -s $EGRESS_SUBNET -j NC-EGRESS-$target" ]; then
  echo "!! egress jump is not the first matching DOCKER-USER rule (got: '$first_match') — backing out." >&2
  iptables -D DOCKER-USER -s "$EGRESS_SUBNET" -j "NC-EGRESS-$target" 2>/dev/null || true
  exit 1
fi

# New policy is live and verified — retire the old slot.
if [ -n "$active" ] && [ "$active" != "$target" ]; then
  while iptables -C DOCKER-USER -s "$EGRESS_SUBNET" -j "NC-EGRESS-$active" 2>/dev/null; do
    iptables -D DOCKER-USER -s "$EGRESS_SUBNET" -j "NC-EGRESS-$active"
  done
  iptables -F "NC-EGRESS-$active" 2>/dev/null || true
  iptables -X "NC-EGRESS-$active" 2>/dev/null || true
fi

# IPv6 default-deny for containers (no IPv6 stack subnets exist; deny outright).
if ! ip6tables -C DOCKER-USER -j DROP 2>/dev/null; then
  ip6tables -N DOCKER-USER 2>/dev/null || true
  ip6tables -I DOCKER-USER 1 -j DROP
fi
echo "    egress policy live on slot NC-EGRESS-$target ($(printf '%s' "$egress_ips" | grep -c .) allowlisted IPs)"

echo "✅ Hardening applied. Prove it: scripts/deploy/verify_hardening.sh <host>"
