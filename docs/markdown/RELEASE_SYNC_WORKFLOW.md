# Release synchronization workflow (local ↔ GitHub ↔ every production platform)

A release is **one commit and one tree**, and it is not finished until every managed place runs it:

| Place | What must hold |
|---|---|
| GitHub `develop` | points at the release commit (`main` is not the deploy branch) |
| **Balance Cloud** production (`/opt/balancecloud`, compose project `balancecloud`) | checked out at the release, `git status` clean, every database upgraded, 0 pending modules |
| **Atlas Core** production (`/opt/atlascore`, a git worktree of the same repository, compose project `atlascore`, its OWN Postgres server) | the same |
| Local shared runtime (`D:\Projects\ncollection-erp`, bind-mounted into the dev `odoo` container) | checked out at the release, no modified tracked files, the local databases upgraded, 0 pending modules |

The two production platforms share the code and nothing else: separate databases, tenants, filestores, secrets,
queues, runners, certificates and backups. One release is deployed to BOTH; a platform left on an older revision is
drift and is reported per platform. A staged rollout (one platform first) is allowed only when the release is
reported as `BALANCE CLOUD DEPLOYED — ATLAS CORE PENDING` (or the reverse) until the second one is done.

A release that is live in production while the local runtime is behind is reported as
**`PRODUCTION DEPLOYED — LOCAL SYNC PENDING`**, never as complete. Business data is never copied between platforms.

## The check (read-only)

```bash
scripts/dev/release_sync_check.sh                        # local + GitHub
NC_PROD_SSH=root@<vps> NC_PROD_SSH_OPTS="-i <key> -o UserKnownHostsFile=<known_hosts>" \
  scripts/dev/release_sync_check.sh [--json]             # + production
```

Run it **before starting work, before a deployment and after one**. It reads git metadata and `ir_module_module`
only (every query is a `SELECT` in a read-only transaction); it never fetches, checks out, upgrades or deploys, and it
prints no secret. Exit status 0 = in sync, 1 = warnings only, 2 = at least one FAIL.

| Check | FAIL when |
|---|---|
| local / each platform commit and tree | not exactly the release (local BEHIND is named with its commit count) |
| local tracked changes | any modified tracked file (the runtime would not be the release) |
| each platform local changes | anything in `git status --porcelain` — production-only code is never allowed |
| each platform runner conf | `addons_path` of `/etc/<platform>/odoo.runner.conf` differs from `config/odoo.prod.conf` |
| every database of every platform (each read on that platform's own Postgres, selected by compose project) | a module pending install/upgrade/remove, an installed `ncollection*` module whose version differs from the RELEASE manifest (= upgrade pending), or one absent from the release |

WARN: untracked files under `custom_addons/` or `config/`, an addon in the checkout that is not in the release,
production not checked (`NC_PROD_SSH` unset), or a release with neither a tag nor a row in the release record below.
The two RED proofs it was accepted on: an expected release of `6f40dc6` FAILs the local commit and tree; a restored
pre-upgrade copy of `awtar` FAILs with `ncollection_core(19.0.1.33.0->19.0.1.34.0)` and two more.

## Lifecycle

1. **Develop** on a feature branch or an isolated worktree off `develop`; small commits; targeted tests; the pre-push
   gates (`make hooks-install`).
2. **Integrate**: the candidate is an exact commit whose range over `develop` contains only the intended commits;
   `develop` is fast-forwarded (never force-pushed). `main` is not touched.
3. **Deploy** (`RUNBOOK_RELEASE_UPGRADE.md`): verify the candidate (commit, tree, bundle SHA-256), back up every
   database + filestore with checksums, rehearse on copies when there is a migration, upgrade each database with an
   explicit module list (never `-u all`), verify health and accounting counts. Do it for **Balance Cloud and then
   Atlas Core** (each: its own backup, its own `git -C /opt/<platform> merge --ff-only`, its own containers); an edge
   change (nginx snippet) goes in before any code that relies on it. Then run the check against production.
4. **Sync local** (same day):
   - `git status`: commit or keep every local change; untracked local files are left alone; no `reset --hard`, no
     forced checkout. Fast-forward the runtime branch to the release (`git merge --ff-only <release>`).
   - Back up `ncollection`, `awtar`, `ncollection_demo` (dump + filestore tar + module states + accounting counts,
     `SHA256SUMS`) under `D:\Backups\local-sync-<release>-<UTC>`.
   - Rehearse on restored copies with the release code, then stop `odoo`, upgrade one database at a time with the
     modules the release changed **that the database has installed**, start `odoo`, restart the provisioning runner,
     reload nginx.
   - Compare accounting counts before/after; run the module tests on a throwaway `nctest`.
5. **Complete**: the check reports 0 FAIL on every place (local, GitHub, Balance Cloud, Atlas Core); tag the release
   `release-<sha>` (local, both production checkouts share one repository, GitHub); add a row to the release record.

## Intentional differences (not drift)

| Difference | Why |
|---|---|
| Configuration values (`config/odoo.conf` dev vs `/etc/balancecloud/odoo.server.conf` / `odoo.runner.conf`) | environment-specific: workers, hosts, credentials. Only behaviour (addons_path) is compared. Secrets never enter git (`.env`, `/etc/balancecloud`). |
| Local AI Gateway (`ncollection-ai-gateway`, mock mode) | development-only; production has no gateway. |
| Installed-module sets | platform databases carry `ncollection_saas` (+ `mass_mailing` on the platform), tenants carry their plan's modules and country package; every database is compared with the code, not with each other. |
| Maintenance administrator | exists in tenant databases only (`awtar` locally); platform databases have none by design. |
| Business data, subscriptions, filestores, passwords, API keys | never synchronized between environments. A production copy is only ever restored into the isolated rehearsal stack. |
| Atlas Core vs Balance Cloud | own `.env` secrets, own `/etc/atlascore` configs (`workers = 2`), platform database `atlas-core`, company Atlas Core (AE/AED), Enterprise plan only (Starter/Growth archived), own certificate (`atlas-core.io` + `*.atlas-core.io`) and daily backup timer. Only code is shared. |
| `NC_TENANT_DB_HEADER=1` | set on each production platform's odoo service once its proxy snippet clears `X-NC-Tenant-DB` (custom domains); never locally unless the local edge strips it too. |

## Release record

A release commit cannot contain its own hash, so this table always lags by one: the row for a release is written by the
next commit that touches this file. The authoritative marker is therefore a **git tag** `release-<short sha>` created at
deployment on the release commit (in the local repository and in `/opt/balancecloud`; pushing the tag is part of the
approved push). The check accepts either a tag on the release or a row here, read from the checkout, from the release
commit and from GitHub `develop`. Only a table row whose first cell is the release counts, never a hash in prose.

| Release | Tree | GitHub develop | Production | Local runtime | Local databases |
|---|---|---|---|---|---|
| `01240df` | `b94043bf` | 2026-11-19 | 2026-11-19 00:13–00:15 UTC, 7 databases | 2026-11-19 14:21 UTC | `ncollection`, `awtar`, `ncollection_demo` upgraded (core 1.34.0, saas 6.30.0, account_reports 1.19.0, branding 1.21.0); backup `D:\Backups\local-sync-01240df-20261119T140601Z` |
| `dbfd986` | `e44a03df` | 2026-11-19 | Balance Cloud 2026-11-19 23:25–23:28 UTC, 7 databases (backup `/root/nc-backups/pre-dbfd986`); Atlas Core created 2026-11-20 from it (clean bootstrap, `atlas-core`) | 2026-11-19 23:35 UTC | `ncollection`, `awtar`, `ncollection_demo`: core 1.34.1; backup `D:Backupsocal-sync-dbfd986-20261119T232935Z` |
| next candidate (this commit) | — | not pushed | not deployed | not synced | — (record written by the next commit; tag `release-<sha>` at deploy) |
