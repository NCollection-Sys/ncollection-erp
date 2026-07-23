# Backup, WAL Archiving & PITR — Runbook (P2-T04)

> Implements **ARCHITECTURE_DATA_PLATFORM.md §5**. Daily dumps alone = 24-hour
> RPO. pgBackRest continuous WAL archiving drops that to **~1 minute**: a tenant
> corrupting data at 16:00 loses minutes, not a business day.

## Tool decision (Rule 2 / OCA check)

**pgBackRest** — chosen by §5 (encrypted repo, S3/B2, parallel restore, delta).
WAL-G was considered and **not** chosen: pgBackRest's stanza model + built-in
`aes-256-cbc` repo cipher + retention expiry match §5 exactly. Not an Odoo module,
so no OCA option applies.

## Topology

```
PostgreSQL ── archive_command ──► pgBackRest ──► repo (posix local | S3/Backblaze B2, encrypted)
              (archive_timeout=60s)               weekly FULL + daily DIFF + continuous WAL
```

Delivered as the **`docker-compose.backup.yml` overlay** (dev/base untouched):
db → `postgres:16 + pgBackRest`, `archive_mode=on`, repo + spool volumes.

## Encryption & config

The repo is `aes-256-cbc` encrypted. The passphrase and (in prod) the S3 creds
come **only from the environment** — never the committed config:

| Env | Meaning |
|---|---|
| `PGBACKREST_REPO1_CIPHER_PASS` | **required** — repo passphrase (`openssl rand -base64 48`) |
| `PGBACKREST_REPO1_TYPE=s3` | prod: switch the repo to S3/B2 |
| `PGBACKREST_REPO1_S3_BUCKET/_ENDPOINT/_REGION/_KEY/_KEY_SECRET` | prod: Backblaze B2 (S3-compatible) |

Dev/test uses the committed posix defaults (local repo volume). Prod sets the S3
env and the same commands work unchanged.

## Setup + schedule

```bash
export PGBACKREST_REPO1_CIPHER_PASS=...    # from the secrets store
docker compose -f docker-compose.yml -f docker-compose.backup.yml up -d db
./scripts/backup/pgbackrest_stanza.sh      # one-time: init repo + verify archiving

# cron on the server:
0 2 * * 0   scripts/backup/pgbackrest_backup.sh full    # weekly FULL (Sun 02:00)
0 2 * * 1-6 scripts/backup/pgbackrest_backup.sh diff     # daily DIFF
*/5 * * * * scripts/backup/wal_lag_check.sh              # WAL-lag alert (>5 min)
```

**Retention:** `repo1-retention-full=2` (2 full sets + WAL → ~7-day PITR window).
Monthly-fulls-kept-6-months is an operational policy layered on top (a monthly
full to a longer-retention path) — see §5; tracked as ops follow-up.

## Restore

```bash
# Point-in-time restore to a SCRATCH instance (safe, never touches live):
./scripts/backup/pgbackrest_restore.sh '2026-07-22 14:30:00+00'

# Single-tenant PITR (§5 nuance — cluster→scratch→pg_dump→restore into live):
./scripts/backup/pgbackrest_tenant_restore.sh albarari '2026-07-22 14:30:00+00'
#   → dumps the tenant as-of T; prints the destructive restore-into-live command.
```

**Live in-place cluster restore** (disaster only) is an incident procedure: stop
Odoo, `pgbackrest --stanza=ncollection --type=time --target='…' --delta restore`,
start Postgres in recovery, verify, resume. Rehearse on staging first.

## Restore discipline (§5.3)

| Drill | Frequency | Owner |
|---|---|---|
| Verify last dumps exist + checksum in B2 | daily | scripted |
| Restore a random tenant to scratch, boot, click through | monthly | DEV-1 |
| Full PITR of the cluster to scratch at an arbitrary T | quarterly | DEV-1 |
| Disaster sim (fresh VPS from backups + git only) | before go-live (P3-T13), then annually | team |

## Boundary (live proof)

`verify_pitr.sh` proves **restore-to-an-arbitrary-timestamp locally** (backup →
WAL → restore into a scratch instance → assert as-of-T state), against a local
repo. The **off-site S3/B2 archive** and the **staging-cluster rehearsal** need
the VPS + a real bucket + credentials — operator steps, same deferral as the
other infra tickets.

## ⚠️ Operational note

If `archive_command` fails (bad cipher pass, unreachable S3), WAL accumulates in
`pg_wal` and can fill the disk. `wal_lag_check.sh` alerts before that bites —
wire it to cron from day one.
