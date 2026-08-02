# Production Server Hardening — Runbook (P2-T08)

> **Scope boundary.** The hardening is **code**: config templates in
> `config/hardening/`, an idempotent `scripts/deploy/harden.sh`, and a
> `scripts/deploy/verify_hardening.sh` evidence generator. Applying it needs the
> **real server** only you can provision (P2-T07's VPS). Until then this runbook
> + the committed checklist below satisfy the "checklist committed" acceptance
> item; the live port-scan / SSH-rejection proof runs on the box.
>
> Implements ARCHITECTURE_SECURITY.md **Layer 5 (Operations)**: *UFW 22/80/443
> only · SSH keys + fail2ban · unattended security updates · secrets lifecycle*.

---

## What `harden.sh` enforces

| Area | Enforced state | Template |
|---|---|---|
| Firewall | UFW default-deny inbound; only **22 / 80 / 443** open | (in `harden.sh`) |
| SSH | key-only, no root, `MaxAuthTries 3` | `config/hardening/sshd_ncollection.conf` |
| Brute force | fail2ban sshd jail (systemd backend) | `config/hardening/fail2ban-sshd.local` |
| Patching | unattended **security** upgrades (no auto-reboot) | `config/hardening/apt-*.conf` |
| Docker daemon | `no-new-privileges`, `live-restore`, log caps | `config/hardening/docker-daemon.json` |
| Secrets | `.env` → mode `600`, owned by the deploy user | (in `harden.sh`) |
| Postgres | never published (compose already does this) | — |

`harden.sh` is **idempotent** — re-run any time to re-assert.

## Apply (one-time, as root on the server)

```bash
# 0. PRE-FLIGHT (critical): the deploy user must already have a working SSH
#    key, or harden.sh refuses (it will not lock you out).
ls -l /home/deploy/.ssh/authorized_keys        # must be non-empty

# 1. Apply.
cd /opt/ncollection
sudo NC_REPO_DIR=/opt/ncollection DEPLOY_USER=deploy ./scripts/deploy/harden.sh

# 2. From ANOTHER machine (true external view), prove it:
./scripts/deploy/verify_hardening.sh staging.ncollectionerp.com
#    …and for a definitive scan:  nmap -Pn staging.ncollectionerp.com
```

## ⚠️ The 8069 / nginx sequencing (read before applying)

The firewall opens **only 22/80/443**, so it **closes 8069**. Tenant traffic is
meant to arrive via nginx on 80/443 — that edge is **P2-T06 (now merged)**. So on
the real box: bring up the nginx edge first, *then* harden.

If you must reach raw Odoo `:8069` during bring-up **before** nginx is serving,
open it to your own IP only, and remove it once nginx is live:

```bash
sudo ufw allow from <YOUR_IP> to any port 8069 proto tcp   # temporary
sudo ufw delete allow from <YOUR_IP> to any port 8069 proto tcp
```

## Known trade-offs (documented, not bugs)

- **Deploy user in the `docker` group** = root-equivalent (needed for CD to run
  `docker compose`). Accepted for now; **rootless Docker** is a future hardening
  pass, out of this ticket's scope.
- **Docker vs UFW:** Docker bypasses UFW for *published* ports. Our compose
  publishes **no** DB port (`ports: !reset []`), so Postgres stays private — but
  never add a `- "5432:5432"` publish on a hardened host expecting UFW to hide it.
- **Auto-reboot is off** — kernel security updates install but a reboot is
  manual. Schedule it; `live-restore` keeps tenants up across the Docker restart.

## Emergency undo (if locked out risk)

```bash
sudo rm -f /etc/ssh/sshd_config.d/60-ncollection.conf && sudo systemctl reload ssh  # restore password auth
sudo ufw disable                                                                    # drop the firewall
```

---

## Committed hardening checklist (acceptance mirror)

Run `verify_hardening.sh <host>` on the provisioned box; every box goes live
only when all are ✅:

- [ ] External port scan shows **only 22 / 80 / 443** (`nmap -Pn <host>`)
- [ ] SSH **password auth rejected** (key-only)
- [ ] `PermitRootLogin no`
- [ ] **fail2ban** sshd jail active (`fail2ban-client status sshd`)
- [ ] Unattended **security** upgrades enabled (`/etc/apt/apt.conf.d/20auto-upgrades`)
- [ ] Docker `daemon.json` applied (`no-new-privileges`, log caps)
- [ ] Postgres **not** reachable from outside (`5432`/`5433` closed)
- [ ] `.env` is mode `600`, owned by the deploy user

## Required tenant-side jobs — what to do when one is reported disabled (#262)

The SaaS-admin tenant list has a **Required Jobs** state. `A required job is off`
means a tenant reported that a job the platform depends on is no longer enabled.
Today that is the auth-log retention purge (`ncollection_auth.cron_gc_auth_log`,
#219), which deletes PII past the retention window.

**It is usually a symptom, not sabotage.** Odoo deactivates a cron by itself
after 5 consecutive failures spanning 7 days
(`MIN_FAILURE_COUNT_BEFORE_DEACTIVATION` / `MIN_DELTA_BEFORE_DEACTIVATION` in
`odoo/addons/base/models/ir_cron.py`). So a job that was failing — most often a
misconfigured `ncollection_auth.log_retention_days` — eventually switches itself
off and goes quiet. **Odoo does not re-enable it when the cause is fixed.**

1. Read the tenant's chatter: the to-do names the job and the tenant DB.
2. Fix the cause first. For the retention purge that is nearly always the
   parameter — a value below the 30-day floor, or non-numeric, both of which
   make the job raise by design (see the auth-log retention rows above).
3. Re-enable by hand in the tenant DB: *Settings → Technical → Scheduled
   Actions → "NCollection: auth log retention purge" → Active*.
4. The next config-sync push clears the state and closes the to-do
   automatically. The nightly reconcile does this within 24h; no action needed.

`Not reported` is **not** the same as healthy — it means the tenant has not
synced since this check shipped, or its config-sync push is failing (check the
Config Sync column, #264). A tenant that legitimately lacks `ncollection_auth`
(provisioned before #178) reports the job as not installed, which is not a
fault — #218 is the ticket that backfills those.

## Out of scope (own tickets / future)

- SSL Labs A grade + headers audit → **P3-T12** · OWASP probing → **P3-T12**
- Rootless Docker; full disk encryption on the VPS volume (note it at provision time)
- CSP tightening (P1-T03 deferred) — edge/app layer, still un-ticketed
