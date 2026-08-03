# Tenant Backup Manager & Restore Drills — Runbook (P2-T05)

> Implements **ARCHITECTURE_DATA_PLATFORM.md §5.1**. This is the *per-tenant*
> layer ON TOP of PITR (P2-T04): cheap single-tenant restores, long-term
> archival, and dev copies. PITR = cluster disaster recovery (RPO ~1 min); this
> = per-tenant granularity. You want both.

## What it does

- **Nightly** `pg_dump --format=custom` per **ready** tenant + a **tar of the
  filestore** (attachments live on disk, not in the DB), bundled + **aes-256-cbc
  encrypted**.
- **Retention pyramid** 7 daily / 4 weekly / 12 monthly per tenant (the nightly
  job types itself: monthly on the 1st, weekly on Sunday, else daily; the prune
  cron enforces the counts).
- **`ncollection.backup`** records every run (size, path, status) and **alerts on
  failure** (chatter + a to-do activity).
- **Restore wizard** → restores a backup into a **scratch/staging DB** (never a
  live tenant — the wizard refuses a live DB name).
- **Monthly restore drill** — restores the newest backup to a scratch DB and
  confirms it is restorable, not merely present (§5.3).

Heavy work runs **off the HTTP workers**: the crons enqueue onto the provisioning
queue channel, executed by the P2-T01 runner, which shells out to the engine
scripts in `custom_addons/ncollection_saas/scripts/backup/`.

## Setup

```bash
# Required: the aes-256-cbc passphrase (secrets store / .env), on the odoo + runner env.
export TENANT_BACKUP_CIPHER_PASS=...        # openssl rand -base64 48

# Prove the round-trip (data + attachments) locally:
./scripts/backup/verify_tenant_backup.sh
```

The crons (`nightly`, `prune`, monthly `drill`) are active on install. Off-site
upload is opt-in: set `TENANT_BACKUP_UPLOAD_CMD` to an executable that takes the
encrypted file path (an rclone/aws-cli wrapper) so no specific CLI is baked into
the image. Dev/test keeps a local copy under `/var/lib/odoo/backups/<db>/`.

## Restore

- **UI:** open a `Tenant Backups` record → **Restore…** → pick a scratch DB name.
- **CLI:**
  ```bash
  docker compose ... exec -e TENANT_BACKUP_CIPHER_PASS=... odoo \
    bash /mnt/extra-addons/ncollection_saas/scripts/backup/tenant_restore.sh \
    <backup.tar.enc> <scratch_db>
  ```
  Restores the DB **and** the filestore under the scratch DB name.

## Notes

- **A live tenant DB can only be restored over by its OWN backup** (#275). The
  rule is enforced in `ncollection.backup._assert_restore_target`, so it holds
  for every caller including raw RPC — not just the UI:
  - **restore wizard** → scratch/staging only; it refuses a live DB name;
  - **monthly drill** → scratch only (`drill_<db>`);
  - **in-place rollback** → allowed, and only with that tenant's own snapshot.
    This is `action_restore_in_place` (#244) or the shell path — the recovery
    route after a failed fleet migration.

  > This bullet previously read *"Never overwrites a live tenant — restores
  > always target a scratch/staging DB."* That became false when #244 added
  > in-place rollback. It is called out rather than quietly corrected because
  > this is the page an operator reads mid-incident, and the old wording invited
  > either false confidence that no in-place restore can happen, or hesitation
  > to use the recovery path that exists for exactly that moment.
- **pg client vs server skew:** the odoo image's newer `pg_dump` emits
  `SET transaction_timeout` that the pinned postgres:16 server rejects harmlessly;
  the restore script judges success by the restored schema, not pg_restore's exit
  code.
- **Complements PITR** — for a "restore the whole cluster to 15:58" incident use
  PITR (P2-T04); for "give me tenant X as of last night" use this.

## OCA / reuse (Rule 5)

Evaluated OCA `auto_backup`: it runs *inside each tenant* (per-DB, local/SFTP, no
S3, no platform orchestration), which contradicts §5's platform-side, per-tenant,
S3, restore-wizard design. **Decision: build custom on native `pg_dump`/`pg_restore`.**
