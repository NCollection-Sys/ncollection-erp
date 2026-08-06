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
| Container egress (#311) | `DOCKER-USER` default-deny; allowlist-only outbound from the egress subnet; IPv6 denied | `config/hardening/egress_allowlist.txt` |

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

## Container egress backstop (#311) — operating notes

`harden.sh` step [7/7] default-denies outbound traffic from the egress subnet
(`172.31.240.0/24`) in the `DOCKER-USER` iptables chain and allows only the
hosts in `config/hardening/egress_allowlist.txt`. This is a **network backstop
behind** the code-level egress controls (ECB pin #308, AI gateway #58), not a
replacement — Docker bypasses UFW's `default allow outgoing`, so without it a
compromised container could reach anywhere.

- **CDN / dynamic-IP drift (expected, not a bug).** Several allowlisted hosts
  are CDN/anycast-fronted (verified: ECB via the ax4z.com CDN; Stripe, Google,
  ACME behind Cloudflare; B2 rotates). Their IPs change without notice. The
  allowlist is resolved **at `harden.sh` run time, not continuously.** When an
  allowlisted call starts failing (`verify_hardening.sh` §D flags the ECB
  reach), the fix is simply to **re-run `harden.sh`** — it re-resolves and
  ping-pongs to a fresh chain with zero policy gap. #311 deliberately does not
  track dynamic IPs; a resolver/cron-refresh mechanism is future work.
- **Persistence across reboot.** iptables rules set here are **not persisted**
  (no `iptables-persistent`). After any reboot, **re-run `harden.sh`** to
  re-assert the egress policy (it is idempotent). Fold this into the post-reboot
  checklist alongside bringing the stack up.
- **Adding a host.** Add the hostname to `egress_allowlist.txt` (keep it
  minimal — every entry widens the surface), commit, deploy, then re-run
  `harden.sh`. If a required host resolves to zero IPv4 addresses, `harden.sh`
  **aborts** rather than install a policy that would silently break it.
- **The AI-gateway provider host** is a **commented, inactive slot** in the
  allowlist; uncomment it only when P5-T02 ships the gateway satellite.

## Known trade-offs (documented, not bugs)

- **Deploy user in the `docker` group** = root-equivalent (needed for CD to run
  `docker compose`). Accepted for now; **rootless Docker** is a future hardening
  pass, out of this ticket's scope.
- **Docker vs UFW:** Docker bypasses UFW for *published* ports. Our compose
  publishes **no** DB port (`ports: !reset []`), so Postgres stays private — but
  never add a `- "5432:5432"` publish on a hardened host expecting UFW to hide it.
  UFW also does not cover container **egress**; that is handled separately by the
  `DOCKER-USER` backstop (see the #311 section above).
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
- [ ] Container egress backstop live (#311): `verify_hardening.sh` **§D** green — single first-matching `NC-EGRESS` jump ending in DROP, IPv6 denied, db-plane service blocked off-host, allowlisted host reachable

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
fault — run the backfill below to fix it.

## Backfilling `ncollection_auth` onto older tenants (#218)

#178 added `ncollection_auth` to `CORE_TENANT_MODULES`, so **new** tenants get
the login-audit trail (`ncollection.auth.log`) and the idle-session timeout by
default. That fix was forward-only: tenants provisioned **before** it do not
have the module and never will on their own. Those tenants have **no
application-level record of who logged in**, and #219's retention purge has
nothing to purge.

Not an emergency — the nginx edge rate-limit already covers brute force on every
tenant — but it is a real gap, and it is cheapest to close before the fleet grows.

### Why this uses the fleet migration and not a script

`odoo -u` **cannot install a module.** Verified in `odoo/modules/loading.py`:
`-u` selects only modules whose state is `installed`/`to upgrade`, `-i` only
`uninstalled` ones. A backfill attempted with an upgrade run would report
success having installed nothing.

So the fleet migration gained an **operation** field. Install runs get the same
machinery an upgrade gets — pre-change snapshot, canary, rolling waves, smoke
probe, auto-restore on failure — which is exactly the "backup checkpoint,
off-peak, deliberate" handling a live-tenant change needs.

`-i` is also inherently idempotent (it only acts on uninstalled modules), so a
re-run cannot disturb a tenant that already has the module.

### The run

*SaaS Admin → Fleet Migrations → New*

| Field | Value |
|---|---|
| Modules | `ncollection_auth` |
| **Operation** | **Install (odoo -i)** — the default is Upgrade, which would do nothing here |
| Canary tenants | pick one low-risk tenant |
| Dry run | **on for the first pass** |
| Auto-restore on failure | leave **on** |

1. **Dry run first.** Every line goes `skipped` with the exact command. Confirm
   it says `-i`, not `-u`.
2. Turn dry-run off and **Start**. The canary runs alone; the fleet rolls out
   only if it passes.
3. Read the lines. Three outcomes:
   - **Done** — installed and smoke-probed.
   - **Skipped** — *"Already installed"*. Expected for most of the fleet on a
     backfill, and **not** a failure; these are checked before any snapshot is
     taken, so they cost nothing.
   - **Failed** — auto-restored from its snapshot if that flag is on. Read the
     line message; the tenant is flagged `error` in the registry.
4. Confirm afterwards in the tenant list: **Required Jobs** should stop reporting
   the auth-log purge as not installed.

Run it off-peak. Each non-skipped tenant takes a full backup first, which is
disk and time.

## Rotating the config-sync master key (#221)

`NC_CONFIG_SYNC_KEY` is the platform master. Every tenant's config-sync bearer is
`HMAC-SHA256(master, "nc-config-sync:" || db)` (#212), and each tenant DB stores
only that derived key's **hash**, written at provisioning.

**So rotating the master breaks every tenant at once** until they are re-keyed —
the platform starts presenting keys derived from the new master while each tenant
still holds the hash of the old one. Every push returns 401.

That failure is quiet by design: `_config_sync_push` never raises, so a lifecycle
transaction cannot be broken by a transport fault. The consequence is what makes
this urgent — config sync is what propagates `action_suspend` / `action_expire` /
plan downgrades into each tenant's `ncollection.workspace.config`, which P1-T10
licence enforcement reads. **A suspended subscription silently fails to lock a
stale-keyed tenant's workspace.** The customer keeps working.

### The rotation

1. **Rotate the master** in the secrets store / `.env`, then restart Odoo so the
   new value is in the platform process environment.
   ```bash
   # .env  (mode 600, deploy user)
   NC_CONFIG_SYNC_KEY=<new high-entropy value>
   docker compose up -d --force-recreate odoo
   ```
2. **Re-key the fleet.** *SaaS Admin → Tenants → (cog) "Re-key config sync (all
   ready tenants)"*. It targets every `ready` tenant regardless of what is
   selected — deliberately, so a rotation cannot silently cover only the page you
   were looking at. Requires Settings-administrator rights, enforced at the ORM
   and not just on the button.
3. **Read the summary.** Three outcomes, and the difference matters:
   - **re-keyed** — key replaced *and* a live verification push authenticated.
   - **skipped** — no config-sync account (see below). Reported, not alarmed;
     these do not keep the summary from going green, because there is nothing
     to rotate.
   - **failed** — makes the summary a sticky warning naming the databases.
     **Failed tenants still hold the OLD key.** The rotation is not complete
     until that list is empty.

   Green therefore means "nothing failed", with any skips stated explicitly.
4. **Confirm independently** in the tenant list: the **Config Sync** column
   (#264) should read `ok` for every tenant, with a fresh *Last OK*.

Steps 3–4 are not ceremony. The re-key job does not report success on a written
key — only on a key that then authenticated — precisely so "the job said OK" and
"the fleet works" cannot diverge.

### Revoking one leaked tenant key

Same operation, one record: open the tenant and press **Re-key config sync** on
the form. Re-deriving replaces the row, so the leaked key stops authenticating
immediately. Nothing else about the tenant is touched — this is not a re-seed,
and the tenant's admin, password and company name are left alone.

### When a tenant reports `REKEY_SKIPPED_NO_ACCOUNT`

That tenant has no `config-sync@ncollection.internal` account — it predates
P2-T03. This is **not** an error, and the job deliberately will not create one:
conjuring a platform-writable account into a tenant that never had one is a
privilege change, not a key rotation. Provision it deliberately instead.

### If the master is lost

There is no recovery path from the tenant side — the stored hashes are one-way
and per-tenant. Set a new master and re-key the fleet (steps 1–4). Until then,
config sync is down for every tenant and plan changes will not propagate.

## Out of scope (own tickets / future)

- SSL Labs A grade + headers audit → **P3-T12** · OWASP probing → **P3-T12**
- Rootless Docker; full disk encryption on the VPS volume (note it at provision time)
- CSP tightening (P1-T03 deferred) — edge/app layer, still un-ticketed
