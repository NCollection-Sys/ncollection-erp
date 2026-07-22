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

echo "==> [1/6] Firewall (UFW): allow 22/80/443, deny the rest"
# Allow SSH BEFORE enabling so an active session is never cut.
ufw --force reset >/dev/null
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

echo "==> [2/6] SSH: key-only, no root (validated before reload)"
install -m 0644 "$HARDEN_DIR/sshd_ncollection.conf" \
  /etc/ssh/sshd_config.d/60-ncollection.conf
sshd -t
systemctl reload ssh

echo "==> [3/6] fail2ban: ssh jail"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq fail2ban
install -m 0644 "$HARDEN_DIR/fail2ban-sshd.local" \
  /etc/fail2ban/jail.d/ncollection-sshd.local
systemctl enable --now fail2ban
systemctl restart fail2ban

echo "==> [4/6] Unattended security upgrades"
apt-get install -y -qq unattended-upgrades
install -m 0644 "$HARDEN_DIR/apt-auto-upgrades.conf" \
  /etc/apt/apt.conf.d/20auto-upgrades
install -m 0644 "$HARDEN_DIR/apt-unattended-upgrades.conf" \
  /etc/apt/apt.conf.d/52ncollection-unattended

echo "==> [5/6] Docker daemon hardening"
install -d -m 0755 /etc/docker
install -m 0644 "$HARDEN_DIR/docker-daemon.json" /etc/docker/daemon.json
# live-restore keeps tenants serving across this daemon restart.
systemctl restart docker

echo "==> [6/6] Secrets: .env 0600, owned by the deploy user"
if [ -f "$REPO_DIR/.env" ]; then
  chown "$DEPLOY_USER:$DEPLOY_USER" "$REPO_DIR/.env"
  chmod 600 "$REPO_DIR/.env"
fi

echo "✅ Hardening applied. Prove it: scripts/deploy/verify_hardening.sh <host>"
