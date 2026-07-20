# OCA Dependencies (ticket P1-T04)

All external (OCA) addon repos are managed by **git-aggregator** from the pin list
**`repos.yml`** at the repo root. `./oca/` is a **generated directory** (gitignored) —
never commit its contents, never edit them by hand.

```bash
make oca        # aggregate the pinned repos into ./oca/  (fresh clone: run this first)
make up         # guarded: refuses to start if ./oca/ is missing
```

Every environment — dev laptops, CI, prod — resolves **the exact same commit** of every
OCA repo, because the pins are commit hashes, not branch names.

## Current pin list

| Repo | Branch line | Modules we consume | Consumer | Sunset |
|---|---|---|---|---|
| `OCA/account-financial-reporting` | 19.0 | `account_financial_report` | financial reports (demo + P3) | **#117** [F2-T07] — retired when native `ncollection_account_reports` reaches parity |
| `OCA/mis-builder` | 19.0 | `mis_builder`, `mis_builder_budget` (+`mis_builder_demo` in demo DBs) | `ncollection_mis_templates` | **#117** |
| `OCA/reporting-engine` | 19.0 | `report_xlsx`, `report_xlsx_helper` | XLSX rendering for the reports above | with #117 |
| `OCA/server-tools` | 19.0 | `auditlog` | audit trail (wired at **P8-T05**) | keep |
| `OCA/server-ux` | 19.0 | `date_range` | dependency of `mis_builder` | keep |
| `OCA/server-auth` | 19.0 | `auth_session_timeout` | `ncollection_auth` (P1-T19 session timeout) | keep |
| `OCA/queue` | 19.0 (pinned `ebb87ea4`) | `queue_job` | `ncollection_saas` provisioning runner (P2-T01) | keep |

Exact hashes live in `repos.yml` (one block per repo — delete the block to drop the repo).

## Update workflow (bump a pin)

1. Edit `repos.yml`: change the commit hash of **one** repo (find candidates with
   `git -C oca/<repo> log --oneline HEAD..origin/19.0` after a `git fetch`).
2. `make oca && make restart` — re-aggregate and smoke-test locally
   (`make upgrade m=<consumer-module> db=<db>` where relevant).
3. Open a PR. CI aggregates the same pins and installs `ncollection_mis_templates`
   (the OCA-consuming module), so a broken bump fails the `test` job.
4. 1 review → merge. Every environment picks up the identical commit.

## Per-database installation

Per the §2.5 matrix (`DELIVERABLE_1_SYSTEM_DESIGN.md`): OCA financial modules are
installed **in tenant DBs only**, by the provisioning engine (P2-T01/02) when the plan
includes accounting. Nothing installs OCA modules into the admin DB.

## Decision record (Rule 2 — OCA-first)

- **Why git-aggregator** (not submodules / vendoring): the OCA-standard tool; exact-commit
  pins with a one-line update path; no 2,000-file vendored diffs in our history (the
  previously vendored, unpinned copies were removed by this ticket — see PR for #5).
- **Version note (2026-07-19)**: pins were set to the 19.0 branch heads of that date; the
  prior vendored snapshot differed only by upstream patch releases
  (`account_financial_report` 19.0.0.0.13 → .15 + new i18n files — diff-verified).
- **Tactical-bootstrap rule**: the reporting repos are a bootstrap until native
  `ncollection_*` modules replace them (**#117**). Keep their blocks in `repos.yml`
  trivially deletable; do not grow new dependencies on them without checking the
  architecture first (Standing Rule 5).
- **queue_job pin (P2-T01)**: `OCA/queue` is pinned to `ebb87ea4`, **not** 19.0 HEAD. The
  next commit adds `openupgradelib` to `queue_job`'s `external_dependencies` — a package
  imported only by the 18.0→19.0 migration scripts (dead code for a fresh-19 platform) that
  would nonetheless block install on the stock `odoo:19` image, forcing a custom Dockerfile.
  Pinning at `ebb87ea4` keeps the stock image. The only other skipped code commit removes a
  multi-db monkey patch; our provisioning runner uses `queue_job` on the **single admin DB**
  (jobs shell out to build tenant DBs), so its cross-db behavior is never exercised —
  verified: `queue_job` installs and functions at this pin. Bump deliberately (and add
  `openupgradelib` to the image) only if a newer `queue_job` feature is ever needed.
- **Brute-force lockout (P1-T19)**: OCA `auth_brute_force` was evaluated and found
  **dead upstream** — it does not exist on any `OCA/server-auth` branch ≥ 12.0
  (an Odoo ≤ 11-era module). Porting a decade-old auth monkey-patch would be
  high-risk custom security code. The documented minimal equivalent is **Odoo
  core's native login cooldown** (`base.login_cooldown_after` /
  `base.login_cooldown_duration`, live-verified at `res.users._on_login_cooldown`),
  armed by `ncollection_auth` and paired with the independent Nginx edge
  `limit_req` (P1-T03) — the two layers ARCHITECTURE_SECURITY.md §6 requires.
  Feature flag: `base.login_cooldown_after = 0` disables the app layer.
