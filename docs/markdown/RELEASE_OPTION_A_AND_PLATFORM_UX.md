# Release: Option A tenant security + Platform UX sprint (local shared runtime)

Branch `feature/tenant-owner-least-privilege` (on top of `feature/uae-einvoicing-foundation-sprint` at `5ce0deb`). Follows
RUNBOOK_RELEASE_UPGRADE.md: backup, deploy, preflight, rehearse on a copy, targeted `-u`, smoke, open. Never `-u all`.

## What changes, by module

| Module | Version | Why it must be upgraded |
|---|---|---|
| `ncollection_core` | 19.0.1.26.0 -> **19.0.1.27.0** | Option A: `group_tenant_admin`, Owner role unlinks `base.group_system` / `base.group_erp_manager`, ACLs, Business Settings, `res.company` delegation, post-migrate enforcement; UX sprint: Apps lock, Developer Mode, `/web/become`, Website placeholder, print mixin |
| `ncollection_branding` | 19.0.1.17.0 -> **19.0.1.18.0** | per-document stamp mixin, Document Layout preview, stamp designer, workspace-administrator hook |
| `ncollection_account_core` | 19.0.1.11.0 -> **19.0.1.12.0** | **stored `nc_stamp_mode` on `account.move` / `account.payment`**, line columns, file names, Business Settings (accounting), Accounting Defaults menu |
| `ncollection_sale_documents` | 19.0.1.1.0 -> **19.0.1.2.0** | **stored `nc_stamp_mode` on `sale.order`**, line columns, file names, Business Settings (sales) |
| `ncollection_purchase_documents` | 19.0.1.1.0 -> **19.0.1.2.0** | **stored `nc_stamp_mode` on `purchase.order`**, line columns, file names, Business Settings (purchase) |
| `ncollection_auth` | 19.0.1.3.0 -> **19.0.1.4.0** | now depends on `ncollection_core`; Workspace Administrator reads the audit log; `admin_settings_change` event |
| `ncollection_api` | unchanged | (an intermediate commit opened API admin to the Owner and was reverted: no net change) |

The stored columns are why these versions MUST move: the release gate compares versions, so without them it would call a
database READY while the columns are missing, and the first invoice read would fail (R-037).

## Pre-existing drift on the shared runtime (not caused by this release, but included in it)

Read-only `make release-status` on 2026-11-03: **`ncollection` (the platform database), `atlas` and `test15` are already
`upgrade_required`** - the release gate serves them a 503 today. Cause: `ncollection_account_localization_uae` (and on
`einvdev` / `einvfull` also `ncollection_uae_einvoicing`) was bumped by the UAE e-invoicing sprint and only `e2euae` was
upgraded. The same drift made the official rehearsal die at step 0b (see below). The `-u` lists below include those modules.

## Database x module matrix (installed versions read 2026-11-03)

`-u` = installed modules among {core, branding, account_core, sale_documents, purchase_documents, auth,
account_localization_uae, uae_einvoicing}; Odoo upgrades only what is installed.

| Database | Kind | Core now | `-u` for this release | Notes |
|---|---|---|---|---|
| `ncollection` | platform | 1.26.0 | core, branding, account_core, sale_documents, purchase_documents, auth, account_localization_uae | **gate 503 today** (localization); migration skips (platform) - **rehearsed: PASS** |
| `atlas` | tenant | 1.26.0 | core, branding, account_core, sale_documents, purchase_documents, auth, account_localization_uae | **gate 503 today** - **rehearsed: PASS**, Owners lose `base.group_system` |
| `test15` | tenant | 1.26.0 | as `atlas` | gate 503 today |
| `e2euae` | tenant (e-invoicing, **protected**) | 1.26.0 | core, branding, account_core, auth, account_localization_uae, uae_einvoicing | **rehearsed on a copy: PASS**; every e-invoicing table and the three XML files identical |
| `einvdev`, `einvfull` | tenant | 1.26.0 | + `ncollection_uae_einvoicing` | localization and e-invoicing already behind |
| `e2eadmin`, `saastest` | platform (suite fixtures) | 1.26.0 | core, branding, auth (as installed) | |
| `e2eclienta`, `e2eclientb` | tenant (suite fixtures) | 1.26.0 | core, branding, account_core / sale_documents (as installed), auth | |
| `atlaseg`, `awtar`, `devpath`, `test12`, `test14`, `uaetest`, `i18nx`, `ncfocus`, `ncollection_demo`, `p1t12_test`, `p2t02_blast`, `t_a` | older fixtures | 1.4.0 - 1.24.0 | many modules behind (up to 13) | already `upgrade_required` before this work; **out of scope**, not upgraded here |

## Sequence (for the authorised maintenance only)

1. Read-only: `make release-status db=<db>` for each in-scope database; record the states.
2. Backup each in-scope database and its filestore (`pg_dump -Fc` + filestore copy) and verify the dump lists.
3. Bring this branch's code into the runtime's tree (the only point where the shared runtime changes).
4. Hold, then targeted `-u` **platform first** (`ncollection`), then each tenant, with the lists above; `scripts/dev/assert_odoo_setup.sh`
   on every log.
5. Per database: `schema_drift.py` empty, nothing `to upgrade`, versions equal the code, release gate `ready`.
6. Restart Odoo once, after all upgrades (the gate reads the code's versions at start).
7. Smoke: login page, sign-in, web client, `/web/health`; one tenant Owner: Business Settings saves, Developer Mode from the
   Admin Center, a technical RPC refused; e-invoicing: the three XML hashes and the validator count unchanged.

**Rollback** (a failed step, never partial): stop, restore the database and filestore from step 2, put the previous code back.
Never "roll back" by re-granting `base.group_system` to Owners.

## Rehearsal evidence (disposable copies, branch code, shared runtime untouched)

| Copy of | Setup log | Versions | Schema drift | `nc_stamp_mode` columns | Owner technical groups after |
|---|---|---|---|---|---|
| `ncollection` | clean | all target | 0 | 4/4 | n/a (platform; platform admin keeps `base.group_system`) |
| `atlas` | clean | all target | 0 | 4/4 | `system=False`, `erp_manager=False`, `tenant_admin=True`; proxy unchanged |
| `e2euae` | clean | all target | 0 | 2/2 (no sales / purchase) | registrant Owner `1 -> 0`; e-invoicing tables identical (ordered by id), XML hashes identical |
| new tenant (`seed_tenant.py`, twice) | clean | - | - | - | registrant: `system=False`, `erp_manager=False`, `tenant_admin=True`; second run no change |

**The official rehearsal's step 0b** (`scripts/upgrade/rehearse_upgrade.sh`) died silently: it set the scratch administrator
through `odoo shell`, which itself crashed with `UndefinedColumn: res_partner.nc_trn` on the stale copy (the drift above), and
the assignment had no guard under `set -euo pipefail`. Fixed on this branch: SQL only, passlib hash, loud failures.
