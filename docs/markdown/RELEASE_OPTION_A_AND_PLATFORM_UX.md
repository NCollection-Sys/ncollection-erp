# Release: Option A tenant security + Platform UX sprint (local shared runtime)

Release revision: **`feature/tenant-owner-least-privilege` @ `4c135e6`** (17 commits on `feature/uae-einvoicing-foundation-sprint`
@ `5ce0deb`, which is what the runtime's tree holds). Follows RUNBOOK_RELEASE_UPGRADE.md: backup, deploy, rehearse on a copy,
targeted `-u`, smoke, open. Never `-u all`. Final gate verified 2026-11-03.

## Modules and versions

| Module | Installed today | Release | Why |
|---|---|---|---|
| `ncollection_core` | 19.0.1.26.0 | **19.0.1.27.0** | Option A role / ACLs / Business Settings / company delegation / migration; UX sprint |
| `ncollection_branding` | 19.0.1.17.0 | **19.0.1.18.0** | per-document stamp mixin, layout preview, designer, workspace-administrator hook |
| `ncollection_account_core` | 19.0.1.11.0 | **19.0.1.12.0** | **stored `nc_stamp_mode`** on `account.move` / `account.payment`; lines, file names, settings, delegation before `account` |
| `ncollection_sale_documents` | 19.0.1.1.0 | **19.0.1.2.0** | **stored `nc_stamp_mode`** on `sale.order` |
| `ncollection_purchase_documents` | 19.0.1.1.0 | **19.0.1.2.0** | **stored `nc_stamp_mode`** on `purchase.order` |
| `ncollection_auth` | 19.0.1.3.0 | **19.0.1.4.0** | depends on core; Workspace Administrator reads the audit log; new event |

No other module changes (`ncollection_saas`: provisioning *script* only; `ncollection_api`: no net change). Pre-existing drift,
also brought current where in scope: `ncollection_account_localization_uae` (19.0.1.7.0) and `ncollection_uae_einvoicing`
(19.0.1.4.0).

## Database scope (every database on the runtime, read 2026-11-03)

The release gate refuses a database whose installed modules are older than the code (branded 503 before the registry), so a
restart on this code WITHOUT upgrading turns every database with `ncollection_core` into 503 - including the five READY today.

| Group | Database | Today | `-u` (exactly) | After |
|---|---|---|---|---|
| A - keep available | `e2euae` (protected e-invoicing) | ready | core, branding, account_core, auth | ready |
| A | `e2eadmin` (platform, suite) | ready | core, branding | ready |
| A | `saastest` (platform, persistent) | ready | core, branding | ready |
| A | `e2eclienta` (suite) | ready | core, branding, account_core, sale_documents | ready |
| A | `e2eclientb` (suite) | ready | core, branding, sale_documents | ready |
| B - platform | `ncollection` (platform) | **503** (localization drift) | core, branding, account_core, sale_documents, purchase_documents, auth, account_localization_uae | ready |
| C - restore | `atlas`, `test15` | 503 (localization drift) | same as `ncollection` | ready |
| C - restore | `einvdev`, `einvfull` | 503 (e-invoicing drift) | core, branding, account_core, account_localization_uae, uae_einvoicing | ready |
| out of scope | `atlaseg`, `awtar`, `devpath`, `i18nx`, `ncfocus`, `ncollection_demo`, `p1t12_test`, `p2t02_blast`, `t_a`, `test12`, `test14`, `uaetest` | 503 (far behind: up to 13 modules) | none | 503, unchanged |
| unaffected | `hd67`, `rtadmin`, `rtclienta`, `rtclientb` | no NCollection module | none | unchanged |

Derived states agree with `make release-status` on every database the tool can query (it cannot query databases without a
config-sync key or with an underscore name; those were derived with the gate's own rule).

## Final-revision rehearsal (every in-scope database, exactly its `-u` list, branch @ 4c135e6, disposable copies)

All ten: upgrade exit 0, `assert_odoo_setup.sh` clean, nothing `to upgrade`, `schema_drift.py` 0, gate rule READY. Owners holding
`base.group_system` directly afterwards = platform entry proxies only (e2eclienta 3, e2eclientb 1, atlas 1, test15 1); real
Owners stripped (test15 2 -> 1 proxy, e2euae 1 -> 0). Measured upgrade time: 355 s in total (27 - 52 s per database).
e2euae copy: every e-invoicing table identical (ordered by id), the three XML files identical.

## Procedure (one maintenance; compose files exactly as the running containers were created)

`F="-f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.routing.yml -f docker-compose.saas.yml -f docker-compose.einvoice.yml"`

1. **Record** `make release-status db=<db>` for the in-scope databases; record e2euae's attachment 548/550/551 SHA-256, the
   validator POST count, and the e-invoice row digest.
2. **Backup** each in-scope database (`pg_dump -Fc`) and its filestore (`tar` of `/var/lib/odoo/filestore/<db>`) into a dedicated
   Docker volume (WSL disk: ~935 GB free; host D: has ~6.6 GB, not used); verify each dump with `pg_restore -l`.
3. **Stop** `ncollection-odoo` and `ncollection-provisioning-runner` (`docker stop`; nginx stays up and answers 502). No process
   may serve old Python against an upgraded database, and the runner's crons bypass the release gate.
4. **Integrate** in `D:\Projects\ncollection-erp`: `git merge --ff-only feature/tenant-owner-least-privilege` (fast-forward,
   no merge commit, no rebase; the three untracked local-only files are not touched - no path collides).
5. **Upgrade**, platform first (`ncollection`), then groups A and C, one database at a time:
   `docker compose $F run --rm --no-deps -T odoo odoo -d <db> -u <list> --stop-after-init --no-http --max-cron-threads=0`,
   each log through `scripts/dev/assert_odoo_setup.sh`; stop at the first failure.
6. **Start** the same two containers (`docker start`; their configuration, env and mounts are unchanged), then `make pdf-hosts`.
7. **Gate**: `make release-status` READY for every in-scope database; out-of-scope databases still 503 (not 500).
8. **Security**: as a tenant Owner, `/web/become` keeps the uid; server action / view / rule / group / ACL writes refused;
   Developer Mode from the Admin Center works and leaves. As the platform admin: Apps visible.
9. **Functional / browser**: Business Settings save and reload; Company saves; invoice Document Stamp Off persists and prints
   without the stamp; Arabic RTL.
10. **E-invoicing**: the three SHA-256 values, the validator count and the e-invoice row digest equal step 1.

**Rollback** (any failed step; never a partial release): stop both containers; `git reset --keep 5ce0deb` on the runtime tree;
restore each upgraded database (`dropdb` + `pg_restore`) and its filestore from step 2; start the containers. Never "roll back" by
re-granting `base.group_system` to Owners.

## Privileged accounts that Option A deliberately does not touch

| Account | Where | Classification |
|---|---|---|
| `platform.uN` (`nc_platform_proxy`) | atlas, devpath, e2eclienta (3), e2eclientb, uaetest | **Authorized platform maintenance**: the platform entry proxy, reached only through a one-time platform grant |
| id 2 = Odoo `base.user_admin` (`admin`, `atlasadmin`, `dev@example.test`, `uae@example.test`) | atlas, devpath, e2eclienta, e2eclientb, einvdev, einvfull, uaetest | **System account requiring special handling**: Odoo's database administrator in databases NOT created by the provisioning engine (dev databases and E2E suite fixtures; the E2E suite signs in with it). In 6 of the 7 it is the only administrator - stripping it would lock the workspace out. A provisioned tenant never has this (the seed turns id 2 into the Owner, then Option A strips it - proven). Remediation, per database, if it is ever to serve a customer: add the Owner role to id 2 and run `res.groups._nc_enforce_owner_least_privilege()` (it becomes a Workspace Administrator; the platform keeps maintenance through the proxy). For E2E fixtures this belongs to the suite's harness. |
