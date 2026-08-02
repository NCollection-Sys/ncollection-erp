# Runbook — Tenant Fleet Migration (P3-T14)

How to roll a **shared-addon** upgrade (`odoo -u <modules>`) across every tenant
database safely. Implements ARCHITECTURE_DATA_PLATFORM §7. Lives in
`ncollection_saas` (`ncollection.fleet.migration` + `.line`).

> **When you need this:** any change to a shared addon (e.g. `ncollection_core`,
> `ncollection_branding`) that is already installed in tenant DBs. A code merge
> updates the *addons on disk*; each tenant DB is only upgraded when an
> `odoo -u` runs against it. This orchestrator does that for the whole fleet.

## Model

- **`ncollection.fleet.migration`** — one run: the module list, wave size, canary
  set, `dry_run`, `auto_restore`, state, and the audit `log`.
- **`ncollection.fleet.migration.line`** — one target tenant: its wave, state,
  pre-upgrade snapshot (`backup_id`), and per-tenant message.

## The algorithm (per §7)

1. **Inventory** — every tenant with `database_status = ready`.
2. **Canary** (wave 0) — the tenants you select. The fleet rolls out **only if
   all canaries pass**.
3. **Rolling waves** — the rest, in batches of `wave_size`.
4. **Per tenant** — pre-upgrade `pg_dump` snapshot (`ncollection.backup`) →
   `odoo -u <modules> --stop-after-init` in an isolated subprocess → smoke probe
   (an ORM read in `odoo shell`) → mark `done`.
5. **Failure isolation** — a tenant that fails is handled per `auto_restore`
   (below); **the wave continues**. One broken tenant never blocks the others.

## Running one (UI)

*Settings → Technical → NCollection SaaS → Fleet Migrations → New*
(needs the **Settings / `base.group_system`** group.)

1. **Modules** — comma-separated technical names, e.g. `ncollection_core`.
2. **Wave size** — tenants per rolling wave (default 5).
3. **Canary tenants** — pick the internal demo tenant + 2–3 volunteers.
4. **Dry run** — leave ON for the first pass: it logs the intended `-u` for every
   tenant and touches **nothing**. Review the target list, then turn it off.
5. **Auto-restore on failure** — see below.
6. **Start (queued)** — runs off the HTTP workers via `queue_job`; the
   `advance fleet migrations` cron (every 2 min) gates the canary and opens each
   next wave. (**Run now (inline)** does the whole thing synchronously — fine for
   a handful of tenants / a staging drill, not for a large fleet on a worker.)

## `auto_restore` — the rollback policy

A pre-upgrade snapshot is **always** taken. On an upgrade/probe failure:

| `auto_restore` | Behaviour |
|---|---|
| **ON** (default) | The engine drops the broken DB and restores its pre-upgrade snapshot **in place** (§7.4), marks the line `restored`, and moves on. Hands-off recovery. |
| **OFF** | The engine marks the line `failed`, **keeps the snapshot**, alerts, and moves on. A human restores it later (the snapshot is on the `ncollection.backup` record). |

Recommendation: **OFF for the first real fleet migration** (watch it, restore by
hand if needed); **ON** once you trust the upgrade.

> The tenant is already broken by the failed `-u`, so restoring its *own*
> pre-upgrade snapshot reverts the failed change — it is recovery, not data loss.

## Manual rollback (auto_restore OFF, or a canary failure)

A failed line **flags its tenant `database_status = error`** and keeps the
pre-upgrade snapshot on its `backup_id`.

### The one-click path (#244)

On the migration form → **Targets**, a failed line with a snapshot shows a
**Restore in place** button (Settings administrators only). It runs the same
`_restore` the automatic path uses — terminate connections, drop, recreate from
the snapshot — then sets the tenant back to `ready` and marks the line
`restored`, with the operator's name in the line message and the run's audit log.

It **refuses** rather than proceed when:

| Condition | Why |
|---|---|
| the line is not `failed` | restoring a successful line would roll back a good upgrade |
| there is no `backup_id` | it failed before the snapshot, so its database was never modified — nothing to undo |
| **the snapshot file is not on disk** | `_restore` **drops first**. Without this check, one click destroys a live tenant and only then finds the backup gone (retention may have swept it) |

Everything written to the tenant since the snapshot is lost — that is what a
rollback means. The button asks for confirmation.

### The shell path (still valid)

Use it when the button refuses for a reason you have resolved out-of-band —
typically a snapshot recovered from off-box backup after retention swept the
local file.

1. Open the failed line → note its **Backup** (`backup_id`) file and the tenant's
   `database_name`.
2. Restore the snapshot **in place**:
   ```bash
   docker compose exec odoo bash \
     /mnt/extra-addons/ncollection_saas/scripts/backup/tenant_restore.sh \
     <snapshot_file> <tenant_db>
   ```
   > ⚠️ Do **not** use the backup **Restore…** wizard here — it deliberately
   > refuses any live-tenant name (it restores to scratch/staging DBs only).
   > In-place tenant recovery is the shell path above, or the button.
3. Then clear `database_status`. **You cannot do this by editing the tenant
   record**: the ISO-1 guard (#228) restricts `provisioning`/`ready` to the
   engine (`env.su`), so a Settings admin editing the field by hand is refused
   with *"Only the provisioning engine may set a tenant's database status"*.
   Use the **Restore in place** button (which holds `sudo()` legitimately), or
   an `odoo shell`:
   ```python
   env['ncollection.tenant'].sudo().search(
       [('database_name', '=', '<tenant_db>')]
   ).write({'database_status': 'ready'})
   ```

Re-investigate the module change before re-running the migration.

## Safety properties

- **No cross-DB ORM** (Rule 3): every per-tenant action is an isolated `odoo`
  subprocess or a psycopg2 maintenance connection; the orchestrator only reads
  the registry and writes its own records.
- **Injection-safe**: DB names are validated (`^[a-z][a-z0-9]{2,62}$`, reserved
  words + the platform DB rejected) before any subprocess arg or `DROP`; module
  names are validated too. No `shell=True`.
- **Admin-only**: both models are `base.group_system`.
- **Audit**: every operation is appended to the run's `log` and posted to its
  chatter.

## Gate rule

> No shared-addon schema change ships to the fleet until the migration has run
> **green on the canary set** (§7). Keep the staging environment's multiple
> tenant DBs for exactly this rehearsal.
