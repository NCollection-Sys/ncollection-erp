# Staging Environment & Continuous Deployment — Runbook (P2-T07)

> **Scope split.** The CD *pipeline* is code and ships in this repo (Dockerfile,
> `docker-compose.staging.yml`, `.github/workflows/deploy-staging.yml`,
> `scripts/deploy/*.sh`). The *server* is real paid infrastructure only you can
> create. Until you do the one-time steps below, the deploy workflow **no-ops
> with a green result** on every merge to `develop` — it never blocks or
> red-spams the branch. Do these steps once and the next merge deploys itself.
>
> **Deliberately out of scope here** (own tickets): the nginx + wildcard-TLS
> edge for `*.staging.ncollectionerp.com` → **P2-T06**; firewall / SSH / secret
> hardening → **P2-T08**; uptime + alerting → **P2-T10**. Until P2-T06 lands,
> staging answers on the host's `:8069`, reachable only to you via the firewall
> rule in step 2.

---

## How the pipeline works

```
merge to develop
      │
      ▼
deploy-staging.yml (GitHub Actions)
  1. secrets present?  no ─► no-op, green ✅ (dormant until you configure staging)
  2. aggregate ./oca (repos.yml pins)
  3. docker build  → ghcr.io/ncollection-sys/ncollection-erp:sha-<gitsha> (+ :develop)
  4. docker push   → GHCR (auth = built-in GITHUB_TOKEN)
  5. ssh staging:  git reset --hard origin/develop && IMAGE_TAG=sha-<gitsha> deploy.sh
                     └─ deploy.sh: compose pull odoo → up -d → smoke-test.sh
  6. Discord notify (success / failure)
```

The **image** (baked app code + OCA + prod config) is the immutable, tagged,
rollback-able artifact. The **server checkout** supplies only the compose files
and deploy scripts for the running commit.

---

## One-time server setup

### 1. Provision the VPS

- Hetzner **CX32** (4 vCPU / 8 GB — per `DELIVERABLE_2_TIMELINE_AND_TOOLING.md` §14), **Ubuntu 24.04**.
- DNS: point `staging.ncollectionerp.com` (and later the wildcard `*.staging...`, P2-T06) at the server IP.

### 2. Base packages, deploy user, firewall

```bash
# as root on the fresh server
apt-get update && apt-get install -y docker.io docker-compose-v2 git
adduser --disabled-password --gecos "" deploy
usermod -aG docker deploy

# Minimal firewall for now (full hardening is P2-T08). Only SSH + the app port,
# and lock :8069 to your own IP until P2-T06 puts nginx/TLS in front.
ufw allow OpenSSH
ufw allow from <YOUR_IP> to any port 8069 proto tcp
ufw --force enable
```

### 3. Deploy key

```bash
# On the server, as the deploy user:
sudo -u deploy ssh-keygen -t ed25519 -f /home/deploy/.ssh/id_deploy -N ""
# Add the PUBLIC key to /home/deploy/.ssh/authorized_keys.
cat /home/deploy/.ssh/id_deploy.pub >> /home/deploy/.ssh/authorized_keys
# You will paste the PRIVATE key (/home/deploy/.ssh/id_deploy) into the
# STAGING_SSH_KEY GitHub secret in step 5.
```

### 4. Repo checkout + runtime env

```bash
sudo -u deploy git clone https://github.com/NCollection-Sys/ncollection-erp.git /opt/ncollection
cd /opt/ncollection
sudo -u deploy git checkout develop
sudo -u deploy cp .env.example .env
# Edit /opt/ncollection/.env — set real DB_PASSWORD, ODOO_ADMIN_PASSWORD,
# NC_CONFIG_SYNC_KEY (see the P2-T03 note in .env.example). IMAGE_TAG can stay
# `develop`; the pipeline overrides it per deploy.
```

### 5. GitHub Actions repository secrets

Repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Value |
|---|---|
| `STAGING_HOST` | `staging.ncollectionerp.com` (or the IP) |
| `STAGING_SSH_USER` | `deploy` (omit to accept the `deploy` default) |
| `STAGING_SSH_KEY` | contents of `/home/deploy/.ssh/id_deploy` (the **private** key) |
| `DISCORD_WEBHOOK` | deploy-channel webhook URL (optional — omit to skip notifications) |

No registry secret is needed: the build pushes to GHCR with the built-in
`GITHUB_TOKEN`. Make the GHCR package readable by the server, or `docker login
ghcr.io` once on the server with a read-scoped PAT if the package is private.

---

## First deploy & verifying acceptance

The acceptance criterion is: **merging to `develop` updates staging within 10
minutes.** After step 5, push any trivial change to `develop` (or re-run the
workflow) and watch **Actions → Deploy to Staging**. Then confirm:

```bash
curl -sf http://staging.ncollectionerp.com:8069/web/health && echo OK
```

## Manual deploy (from the server)

```bash
cd /opt/ncollection
git fetch origin develop && git reset --hard origin/develop
IMAGE_TAG=develop ./scripts/deploy/deploy.sh   # or a specific sha-<gitsha>
```

## Rollback drill (documented rollback — acceptance sub-item 4)

Each deploy records the tag it replaced. To go back one version:

```bash
cd /opt/ncollection
./scripts/deploy/rollback.sh                 # previous tag (auto)
./scripts/deploy/rollback.sh sha-1a2b3c4d5e6f # or an explicit known tag
```

`rollback.sh` reuses `deploy.sh`, so the rollback is smoke-tested the same way.
Image tags are immutable per commit, so any previously built `sha-<gitsha>` is a
valid rollback target for as long as it is retained in GHCR.

---

## Troubleshooting

| Symptom | Check |
|---|---|
| Workflow green but nothing deployed | `STAGING_HOST` / `STAGING_SSH_KEY` not set → the no-op path ran (see the job's `::notice::`). |
| `Permission denied (publickey)` | Public key not in the deploy user's `authorized_keys`; or wrong `STAGING_SSH_USER`. |
| `deploy.sh` fails at `compose pull` | The GHCR package is private and the server can't read it — `docker login ghcr.io` on the server. |
| Smoke test times out | `docker compose -f docker-compose.yml -f docker-compose.staging.yml logs odoo` on the server. |
