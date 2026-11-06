# Local upgrade of `ncollection`, `ncollection_demo`, `awtar`: rehearsal results and the execution / rollback plan

Release line: `feature/63-p5-t06-ai-chat-widget-integration` (contains `feature/bank-reconciliation` @ `24897a8`). **The shared upgrade has not been run and is
not authorised.** Scope status of every requirement: `RELEASE_SCOPE_STATUS.md`.

## 1. What was rehearsed, and how

Isolated stack: its own Postgres on an `--internal` Docker network (Odoo cannot resolve or reach anything outside it; checked), databases restored from the VERIFIED
backups (`bank-release-20261106`, `extra-demo-awtar-20261106`), crons / mail servers / queue jobs neutralised in the restore transaction
(`scripts/upgrade/neutralize_copy.sql`), the release tree, the release `config/odoo.conf` and a copy of `./oca` plus the two pinned repos from the recovery tars
(`account-reconcile` e3d99f3d, `bank-statement-import` aa647ee5). The AI gateway ran as a container of the same stack with the MOCK provider and a key generated
for the run. Nothing in the shared runtime, its databases, backups or bundles was touched.

## 2. The matrix (exactly this, one pass per database, then translations)

`BR` = `ncollection_dashboard_export, ncollection_bank_reconciliation, ncollection_ai` (the sale bridge of bank reconciliation follows by `auto_install`).
`ncollection_ai` depends only on `ncollection_core`, present in all three, so it is installed in all three.

| Database | `-u` | `-i` |
|---|---|---|
| `ncollection` (platform) | `ncollection_core, ncollection_branding, ncollection_account_core, ncollection_account_reports, ncollection_account_dashboard, ncollection_subscription` | `BR` |
| `awtar` | `ncollection_account_core, ncollection_account_dashboard, ncollection_account_localization_uae, ncollection_account_reports, ncollection_auth, ncollection_branding, ncollection_cheque_print, ncollection_core, ncollection_purchase_documents, ncollection_sale_documents` | `BR` |
| `ncollection_demo` | `ncollection_core, ncollection_branding, ncollection_saas, ncollection_subscription` | `ncollection_account_core, ncollection_account_reports, ncollection_account_dashboard, ncollection_sale_documents, ncollection_purchase_documents, ncollection_auth, ncollection_sale_attachments, ncollection_stock_barcode, BR` |

Then, per database: `ir.module.module._update_translations(['ar_001'])` for the installed `ncollection_*` modules (`-u` alone misses new Arabic terms).
Never `-u all`. `ncollection_uae_einvoicing*` and `ncollection_audit` are installed in none of the three and are not touched.

## 3. Results (final integrated code, fresh restore of each database)

| | `ncollection` | `awtar` | `ncollection_demo` |
|---|---|---|---|
| upgrade exit / time | 0 / 79 s | 0 / 60 s | 0 / 76 s |
| `assert_odoo_setup.sh` | clean | clean | clean |
| pending module states | 0 | 0 | 0 |
| schema drift (modules / columns / tables) | 0 | 0 | 0 |
| second run identical (modules, schema, data fingerprints); migrations re-run | yes; 0 | yes; 0 | yes; 0 |
| installed modules before -> after | 174 -> 187 | 122 -> 135 | 125 -> 150 |
| business data before -> after | unchanged | unchanged | unchanged except +1 partner / +1 user: the `cron@ncollection.internal` scheduler service user created by `ncollection_core` data |
| attachments with a file | 852 -> 852 | 567 -> 562 (web asset bundles regenerated) | 874 -> 887 (assets of newly installed modules) |
| bank suite (17 checks, EN / AR, desktop / 390 px) | 17/17 | 17/17 | 17/17 |
| dashboards, PDF / Excel export EN / AR, overpayment, permissions, tenant isolation, **AI widget EN + AR streamed, private rows, forged rows refused** | 21/21 | 21/21 | 21/21 |

No check was skipped; the AI widget checks are part of the 21 on every database. The eight documented `ncollection_demo` attachment defects are unchanged and not repaired
(6 absent files, 60 rows, ids 707 - 766; 2 zero-byte files, 11 rows, ids 546 - 556). `ncollection` receivables 1,909.30 equal the ledger SQL.

Multi-tenant streaming probe (`e2e/rehearsal/multi_tenant_probe.js`, ONE server holding all three databases, `--workers=3`, answers of about 7.7 s): server-wide cap 1.
Of three simultaneous streams (one per tenant) exactly one was admitted; the other two were refused with the friendly message after 379 and 790 ms; an ordinary request
on every tenant took 19 ms while the stream ran (idle 18 - 25 ms); a new stream was admitted afterwards (no leaked slot). 5/5.

## 4. Failed, skipped, and what the rehearsal found

* **No test skipped, none failed.** `ncollection_ai` module tests: 160 run, 0 failed (includes the admission tests); gateway: 51 (tenant-name positive / negative and cross-tenant impersonation tests included).
* **Tenant names.** The gateway now accepts a database name made of ASCII letters, digits and underscores, starting with a letter or digit, 1 - 63 characters
  (`ncollection_demo` works). Hyphen, dot, slash, quotes, whitespace, newlines, a leading underscore, non-ASCII and 64+ characters are refused. Identity is still
  proven by the HMAC under a key derived from the exact name; names that differ only by underscores get different keys and cannot sign for each other (tested).
* **The release gate demands modules that `-u` does not install** (`ncollection_sale_attachments`, `ncollection_stock_barcode` on `ncollection_demo`: branded 503 until installed). They are in the matrix.
* **The runtime `./oca` lacks the two new OCA repositories.** `config/odoo.conf` (which already lists them on this branch) is bind-mounted as a single file: the
  Odoo container must be RECREATED, never restarted (#427).
* **Streaming admission** is per database AND server-wide: `RELEASE_SCOPE_STATUS.md` section 7. The dev runtime runs threaded (`workers = 0`): the server-wide cap is then 2.
* Not rehearsed: anything needing a live AI provider or A-Cube (not permitted); the shared stack's nginx and PDF host names (`make pdf-hosts` after the restart).

## 5. Execution plan for the shared local upgrade (each step needs the owner's go; stop at the first failure)

Preconditions: Windows security gate passed (Tamper Protection and cloud protection on, owner decision on the unrotated credentials), commit headroom >= 6 GB,
backups verified (`sha256sum -c`), bundle of the release tip verified.

1. **Record** `make release-status db=<db>` for the three databases and the module versions; record the SHA-256 of the protected e-invoice attachments 548 / 550 / 551 in `e2euae` (read-only; that database is not upgraded).
2. **Backup** `ncollection`, `ncollection_demo`, `awtar`: `pg_dump -Fc` + `tar` of each filestore into a dedicated Docker volume, `pg_restore -l` each dump, SHA-256 each file. The 2026-11-06 backups are the fallback; re-take them immediately before the change.
3. **OCA**: extract `account-reconcile-e3d99f3d.tar` and `bank-statement-import-aa647ee5.tar` into `./oca/` (`git -C oca/<repo> rev-parse HEAD` must equal the pin). This is the only change to `./oca`.
4. **AI gateway configuration (development-only, mock provider)**: `make ai-key` (`scripts/ai/ensure_dev_gateway_key.sh`) writes a NEWLY GENERATED `NC_AI_GATEWAY_KEY` into the gitignored `.env`
   (kept if already present; never printed; refuses a tracked file, a non-mock `NC_AI_PROVIDER` or any `NC_AI_API_KEY`). `docker-compose.yml` already passes the key to Odoo and
   `docker-compose.ai.yml` to the gateway, which runs the mock provider and has no database network. No production secret is read or reused.
5. **Stop** `ncollection-odoo` and `ncollection-provisioning-runner` (`docker stop`); nginx stays up (answers 502). No old code may serve an upgraded database.
6. **Integrate** the code: `git merge --ff-only feature/63-p5-t06-ai-chat-widget-integration` in `D:\Projects\ncollection-erp` (the runtime tree is at `5decb87`; the three untracked local files do not collide). Run `assert_oca_present.sh`.
7. **Upgrade**, platform first: `ncollection`, then `awtar`, then `ncollection_demo`. For each: `docker compose <the five compose files the runtime was created with> run --rm --no-deps -T odoo odoo -d <db> -u <list> -i <list> --stop-after-init --no-http --max-cron-threads=0`, the log through `assert_odoo_setup.sh`, then the translation step, then `schema_drift.py` (must print 0).
8. **Recreate** (not restart) the Odoo and runner containers so the new `odoo.conf` and the key are live: `docker compose <five files> -f docker-compose.ai.yml up -d --force-recreate odoo ai-gateway` (and the runner); `make pdf-hosts`; `assert_config_mounts_fresh.sh`.
9. **Gate**: `make release-status` READY for the three; the other databases still answer the branded 503 (not 500) and are untouched.
10. **Browser review** (section 6). The bank fixture is NOT run on the real databases (it creates a company); review by hand. Ask one suggested question per database in the chat widget (mock answers).
11. **E-invoicing check**: the three SHA-256 values for 548 / 550 / 551 equal step 1 (nothing should have touched `e2euae`).

**Rollback** (any failed step; never a partial release): stop the containers (including `ai-gateway`); `git reset --keep 5decb87` on the runtime tree; for each upgraded database `dropdb` +
`pg_restore` from the step-2 dump and extract its filestore tar; the two OCA directories and the `.env` key may stay (harmless); recreate the containers; run `make release-status`.
Time: upgrade 3 x about 70 s; backup and restore a few minutes each. Never roll back by granting `base.group_system` to Owners.

**Space and memory**: the three databases are about 0.3 GB of dumps plus about 0.1 GB of filestores (0.5 GB with slack); keep the backup volume in Docker (the host has
D: 6 GB and C: 13 GB free). Memory: upgrade container 2.5 GB plus Postgres; commit headroom measured 7.3 GB with the Docker stack running; the gate is 6 GB.

## 6. What to look at in the browser after the upgrade (real databases)

`awtar`: Accounting dashboard (bank journal card: Upload, Transactions, To Reconcile, Statements), the Invoicing and Sales premium dashboards with PDF and Excel export,
an invoice with an over-payment, the chat widget (English and Arabic), Arabic through the language switch. `ncollection`: the platform administration plus the same accounting screens and the widget.
`ncollection_demo`: dashboards, invoices (it now has the accounting layer) and the widget.
