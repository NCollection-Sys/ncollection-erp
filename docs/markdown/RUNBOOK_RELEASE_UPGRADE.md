# Release runbook: deploying new code to a database that already exists

Why this page exists. New code does not change an existing database: only an explicit module upgrade (`-u`) does, and
Odoo never compares the version a database is at with the version of the code that serves it. Put newer code under an
un-upgraded database and the first request that reads a changed model used to answer **HTTP 500**
(`psycopg2.errors.UndefinedColumn`) while `/web/health` stayed green. This is R-037, and it is what broke the sign-in
page and Company Entry on `atlaseg`. Since the **release compatibility gate** (`RELEASE_COMPATIBILITY_GATE.md`) such a
database answers a branded HTTP 503 maintenance page instead, by itself, from the restart on; databases the code is not
ahead of keep serving. The sequence below upgrades each gated database and opens it again only once it validates.

**Never `-u all`. Never `-u base`. Never upgrade `ncollection_core` on its own. Never edit the schema by hand. Never let
a web request upgrade anything.** The upgrade is a decision, taken by a person, for the modules the preflight names,
after a rehearsal passed. Nothing in the product upgrades a database by itself: the registry-load drift check only
logs, the preflight only reads, the rehearsal only touches its own copy.

## The sequence

```
backup -> deploy code + restart (the gate now holds every database the code is ahead of) -> release plan (read-only)
  -> rehearse on a COPY -> targeted upgrade of exactly the named modules (platform database first, then Fleet
  Migration per tenant) -> preflight again -> HTTP smoke -> critical endpoint smoke -> opened (READY) -> traffic
```

| # | Step | Command | Must be |
|---|---|---|---|
| 0 | **Edge maintenance** only if the release changes the edge itself; the gate protects each database on its own | the edge's maintenance page | as needed |
| 1 | **Backup** every database the release touches, and its filestore | your backup procedure (`scripts/backup/`) | restorable; verified |
| 2 | **Deploy the code and restart the odoo container and the provisioning runner**, so the running processes load it | deploy, then restart `odoo` (+ `provisioning-runner`) | every database the code is ahead of now answers 503 `X-NC-Workspace-State: upgrade_required`; the others serve |
| 2b | **Release plan** (read-only): every registered tenant and the platform database, the modules each needs, both versions, its state | `scripts/upgrade/release_plan.sh` | the plan to execute, deterministic; `ERROR` rows (unreachable, registered but missing, module not deployed) are a stop for that database |
| 3 | **Preflight**: which installed modules are behind the code? Read-only, opens no registry | `make upgrade-preflight db=<db>` | exit 1 + `PREFLIGHT_MODULES=<list>`: step 4 is required, with that list. Exit 0: nothing to upgrade. **Exit 2: stop** (it could not check: no connection, nothing checked, or an installed `ncollection_*` module has no code on disk) |
| 4 | **Rehearse on a copy**: copy + neutralise, upgrade exactly the modules the preflight names, drift report + preflight on the copy, HTTP smoke, Company Entry probe for a tenant | `make upgrade-rehearse db=<db>` | `REHEARSAL=PASS`, and note its `REHEARSAL_UPGRADE_MODULES=<list>` line. `FAIL` (or a refusal, exit 2) is a hard stop: nothing real has been touched |
| 5 | **Targeted upgrade of the real database**: exactly the list step 4 printed, all together, in one run | the command below | exit 0, setup guard clean, no traceback |
| 6 | **Preflight again** | `make upgrade-preflight db=<db>` | exit 0 |
| 7 | **HTTP smoke** of the real stack (from inside the network) | `/web/health` 200, `GET /web/login` 200, a sign-in, `/odoo` 200, session info carries the system brand, `/ncollection/brand/logo` 200 | no 5xx anywhere |
| 8 | **Critical endpoint smoke**: for a tenant, Company Entry from the platform (grant, redeem, tenant Home, exit, single-use token); for the platform, its SaaS admin screens | what the rehearsal ran against the copy, now against the real host | every step answers as on the copy |
| 9 | **Opened**: the tenant's gate answers READY (Fleet Migration opens it after its probe; a held tenant is opened with `POST /ncollection/release/open`, which refuses while the code is still ahead) | the tenant form's Release state | READY; the tenant's users are back |

### Tenants: Fleet Migration does steps 5-9 per tenant

A Fleet Migration line (operation *upgrade*) takes **that tenant's** module set from its gate, refuses if the migration does not cover it (the
modules must move together), holds the tenant `upgrading`, runs `-u` of exactly that set, smoke-probes it, and opens it only when the gate answers
READY. A failure leaves that tenant held `upgrade_failed` (restored from its snapshot with `auto_restore`), the reason on its form; the other
tenants of the wave continue. One tenant behind never takes the platform down: each database is gated on its own.

### The platform database first

It is the control plane (Fleet Migration, Company Entry and provisioning run from it) and it is gated like any other database: behind the code, its
own screens answer the maintenance page until it is upgraded with the command below, after its rehearsal. **Fleet Migration refuses every tenant
migration until the platform is READY.** While its screens are blocked, read it - and hold / open it - through the release channel, which answers
before the registry (`RELEASE_COMPATIBILITY_GATE.md` "Operator status"):

```bash
make release-status db=ncollection
```

`RELEASE_REQUIRED=` is the exact list for the command below. Hold it `upgrading` first, open it after the smoke; `make upgrade-platform-verify`
proves the whole sequence on a scratch copy of the platform database before you do it for real.

### The upgrade command (step 5)

The module list is **never written by hand and never taken from an earlier release**: it is the `PREFLIGHT_MODULES=`
list of step 3, which the rehearsal re-derives on the copy and prints as `REHEARSAL_UPGRADE_MODULES=` (the two must
agree; if they do not, stop). The connection is the container's own environment; nothing secret goes on the command line.

```bash
db=<db>; modules=<the REHEARSAL_UPGRADE_MODULES list, comma separated>
docker compose exec -T odoo sh -c \
  'export PGHOST="$HOST" PGUSER="$USER" PGPASSWORD="$PASSWORD";
   exec odoo -c /etc/odoo/odoo.prod.conf -d "$1" -u "$2" --no-http --stop-after-init --max-cron-threads=0' \
  _ "$db" "$modules" > "upgrade-$db.log" 2>&1
scripts/dev/assert_odoo_setup.sh "upgrade-$db.log" "$db -u $modules" ""
```

* `-c /etc/odoo/odoo.prod.conf` is the production configuration (`docker-compose.prod.yml`); drop it on the dev stack.
* `--no-http` keeps the one-shot process off the port the live server holds; `--stop-after-init` makes it exit when the
  upgrade is done; `--max-cron-threads=0` keeps it from running crons.
* `assert_odoo_setup.sh` is required, because Odoo exits 0 even when a module failed to load (#385).

**All the named modules, together, in one run.** Odoo orders one list by its dependency graph; it cannot order
separate runs. Upgrading one of them alone can abort: `ncollection_core` alone on a database that is also behind on
`ncollection_branding` fails with a `ParseError` (its views reference records that only the branding upgrade creates),
which is exactly how `atlaseg` (behind on core, branding, auth and account_core) stayed on HTTP 500. The tooling refuses
`all`, `base`, and any module outside the NCollection / OCA addons; an Odoo core module that is behind (after an image
change) is a separate, explicit decision this runbook does not cover.

### Many tenants: Fleet Migration

For the fleet, use Fleet Migration (`ncollection.fleet.migration`) with the module list the preflight names: canary
tenants first, then waves, a snapshot before each, failure isolation. Steps 3 and 4 apply per database: preflight every
tenant (the lists can differ, and each tenant gets the list for ITS database, or the union when one run covers several),
rehearse a copy of a representative one of each list.

## What each gate catches, and what it does not

| Gate | Catches | Does not catch |
|---|---|---|
| **Release gate** (`ncollection_core/release_gate.py`, before the registry, on every request) | a database the code is ahead of, mid-upgrade, held, with a module not deployed, or with a schema gap found at registry load: its users get a branded 503, never the 500; the platform reads the exact reason on `/ncollection/release/status` | a database AHEAD of the code (restore the backup together with the code to roll back) |
| Registry-load log (`SCHEMA DRIFT in database ...`, ERROR, once per registry load) | a database already serving that is behind or missing schema, named, with the repair | anything before it is loaded; it only logs, it never upgrades and never raises (its SQL runs in a savepoint, so a failure cannot abort the registry load) |
| Preflight (steps 3, 6) | an installed NCollection / OCA module whose recorded version is older than the code; nothing checked, or an installed `ncollection_*` module without code (exit 2) | a missing column at an unchanged version (that is the registry check and the rehearsal); an Odoo core module (not its universe) |
| Rehearsal (step 4) | the wrong module list (a wrong-module upgrade leaves drift: `FAIL`), a migration that cannot run on real data, every endpoint that 500s on the copy, including Company Entry; an Odoo core module behind (refused) | anything specific to production traffic or data volume |
| Upgrade suite arms 0 and 6-7 (`scripts/upgrade/verify_upgrade.sh`, part of `make verify-all`) | that the gates above keep working: their own rules (arm 0), a fabricated stale database detected, the 500 reproduced, cured by a targeted upgrade, an insufficient upgrade refused, every unsafe target refused, a copy neutralised in the transaction that creates it | |

The rehearsal and the preflight exit non-zero on failure. Neither has a flag that downgrades a failure to a warning.

## What the rehearsal does to its copy (and never to anything else)

* **The source is only read** (`pg_dump` and a copy of its filestore). The copy is `upgrreh<source>`. The rehearsal
  refuses, and drops nothing, if that database or its filestore already exists, if another rehearsal holds the lock
  (`/tmp/nc_rehearse_upgrade.lock` in the odoo container), or if the source is not a plain lowercase alphanumeric name.
  It drops only what it created. `--in-place` works only on the upgrade suite's fixtures, by exact name.
* **The copy does no live work.** A copy carries its source's crons (on the platform: fleet migration waves that run
  `-u` against real tenants, config-sync into real tenants, backups, dunning), queue jobs and mail servers, and the
  shared Odoo server runs the crons of every database it can list. So the restore and a kill switch
  (`scripts/upgrade/neutralize_copy.sql`: every cron off with its next call pushed out and its triggers removed, queue
  jobs cancelled, mail servers and fetchmail off, queued mail cancelled) commit in **one** transaction; Odoo's own
  `odoo neutralize` runs next; the rehearsal then proves `0 active crons, 0 runnable queue jobs` before any Odoo
  process serves the copy, and again right after the upgrade. The scratch server runs with `--max-cron-threads=0` on
  `127.0.0.1` only. Residual, stated: a cron the upgrade itself **creates** (new in that release, due at once) is active
  on the copy from the upgrade's commit until the re-neutralisation a few seconds later.
* **No credential.** The connection is the odoo container's `HOST` / `USER` / `PASSWORD` (as PG* variables) and the db
  container's `POSTGRES_USER`; it refuses when they are missing. A guard (`invariants.py`,
  `rule_release_tooling_carries_no_db_credentials`) keeps a literal out of the release tooling.
* It runs Odoo with the container's default configuration. On a production stack the real upgrade (step 5) names
  `odoo.prod.conf`; check that the two agree on `addons_path` and `data_dir` before relying on the rehearsal there.

## Rolling back

Restore the step 1 backup and redeploy the previous code. A module upgrade is not reversible by downgrading the code:
the migrated schema and data stay.

## A module left mid-upgrade (`to upgrade` / `to install` / `to remove`)

An `-u` process that was killed (OOM, a stopped container) can leave module rows in a transitional state. The release gate reports the database `UPGRADING` (HTTP 503, **no cron job runs**) until those rows change. When those rows are the ONLY problem (no hold, nothing behind, nothing missing, no schema gap) the cron entry still hands the database to Odoo's own `IrCron._check_modules_state`, unchanged: it skips it, and after Odoo's MAX_FAIL_TIME (5 hours) runs Odoo's own `reset_modules_state` - Odoo's behaviour, not a repair of ours; no job runs until the database is READY. Do not wait for it. What to do: read `RELEASE_TRANSITIONAL` from `make release-status db=<db>` (or the tenant's Release section), then run the targeted upgrade of exactly the listed modules through the normal steps above (backup, rehearse, hold, `-u <modules>`, validate, open). The `-u` run completes the transition; never edit the rows by hand.
