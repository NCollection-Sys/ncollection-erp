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
| `OCA/account-financial-tools` | 19.0 (pinned `fde45b2c`) | `account_asset_management` | `ncollection_account_assets` (F4-T03 / #122) | keep — see the decision record below |
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

## F4-T03 Fixed Assets (#122) — ADOPT `account_asset_management`, wrap it natively

This is the first OCA repo pinned since P2-T01, and the **first ADOPT verdict after
two consecutive BUILD-CUSTOM ones** (#120, #121). The difference is worth recording,
because "OCA-first" has been the rule throughout and the answer still changed.

- **Odoo 19 Community ships no fixed assets at all.** `account_asset` moved to
  Enterprise. Verified against the running image: no `account_asset` directory
  anywhere under the shipped addons, and no addon defines an `account.asset` model.
  Standing Rule 2 ("extend before replacing") therefore has no core to attach to —
  the same finding #121 made for budgeting.
- **Why ADOPT here when #121 said BUILD.** `account_budget_oca` models a budget line
  with a *single* `analytic_account_id`, which cannot express FPA's "Department AND
  Cost Center" filtering and does not compose with the `analytic_distribution`
  dimension model #120 established. `account_asset_management` inherits
  `analytic.mixin` on **both** `account.asset` and `account.asset.profile`, i.e. it
  carries `analytic_distribution` — the identical mechanism. The disqualifier does
  not reproduce, so the conclusion flips.
- **What it earns us.** Depreciation boards (linear, linear-limit, degressive,
  degressive-linear, degressive-limit), posting each period through a real
  `account.move`, and disposal with plus-/min-value recognition. FPA §4 puts
  accounting-engine logic on Odoo's side of the line ("Odoo owns accounting;
  NCollection owns business experience"), and a depreciation schedule posting
  double-entry is engine logic — reinventing it would be large, and wrong in ways
  that only surface at a year-end.
- **Health**: "Mature" development status, `19.0.1.0.2`, repo HEAD 2026-08-07 and
  module commits within the last two months. Its only new transitive need is
  `report_xlsx_helper`, already available through the pinned `OCA/reporting-engine`.
- **Not a sunset pin.** Unlike `account_financial_report` and `mis_builder` (both
  inside #117), this is not a tactical bootstrap awaiting a native replacement. It
  sits permanently on the Odoo side of FPA §4.
- **What OCA does NOT give us**, and `ncollection_account_assets` therefore owns:
  the account-type guard (below), a transfer flow (OCA has register / depreciate /
  remove and no transfer), and the Asset Register on the F2 engine (OCA ships its
  own XLSX report, which FPA's reporting ownership rules out).

### The configuration hazard this pin introduces, and the guard for it

`account_asset_management`'s three profile accounts — asset, accumulated
depreciation, depreciation expense — are unconstrained `Many2one('account.account')`.
Nothing upstream enforces their **account types**, and two reports already shipped
here depend on them:

- **#114's cash flow** adds `expense_depreciation` back in Operating and removes it
  from Investing. Because that is equal and opposite, a wrongly-typed account leaves
  the statement **balanced and reconciling** while the Operating/Investing split is
  wrong. `cash_flow.py` states the assumption in prose, unchecked.
- **#411's BS/P&L maps** place `expense_depreciation` in Operating Expenses and
  `asset_fixed` in Non-Current Assets; a charge landing on `expense_direct_cost`
  moves into Cost of Sales and changes the Gross Margin KPI `ncollection_account_analytics`
  publishes (#120).

`ncollection_account_assets/models/asset_profile.py` turns that prose into a
`ValidationError`, and a test asserts the guard's classification still equals
`cash_flow.py`'s tuples — so neither side can drift alone and leave a guard
protecting nothing (#330/#348/#311 are three guards in this repo that did exactly
that).

### The cron this pin drags in, and why it must stay inactive

`account_asset_management` ships `ir_cron_assets_generator` (`data/cron.xml`),
which runs `account.asset.compute.asset_compute()`:

```python
assets = self.env["account.asset"].search([("state", "=", "open")])
created_move_ids, error_log = assets._compute_entries(self.date_end, ...)
```

**No batch limit, no time-box** — every open asset in the database, then a posted
`account.move` for each. `config/odoo.prod.conf` runs `max_cron_threads = 1` with
`limit_time_real_cron = 3600`: ONE shared cron thread serving every tenant
database on the node, sequentially. An unbounded job there delays every other
tenant's crons — license enforcement, config-sync reconcile — by up to an hour.
That is precisely the defect #310 fixed for the ECB fetch, and
`DESIGN_CRON_AND_QUEUE_TOPOLOGY.md` exists because of it.

It ships `active=False`, and `ncollection_account_assets`'
`tests/test_review_round.py::TestOcaCronStaysInactive` asserts that on install.
The record is `noupdate="1"`, so an upgrade will not re-activate it — but a
future pin bump could change the shipped default, and without the test nothing
would notice.

**Enabling it is the intended OCA path, and here it is not safe.** Making
automatic depreciation posting safe on this platform means a batched,
tenant-scoped job on the queue runner, which is a build of its own and outside
F4-T03's acceptance ("depreciation entries posted via standard `account.move`" —
satisfied today by the wizard). Tracked separately so that "ships inactive"
cannot quietly read as "handled": **tenants currently have no sanctioned
automatic depreciation posting.**

### Licensing

`account_asset_management` is **AGPL-3**. Five of the seven previously pinned OCA
modules are already AGPL-3 (`account_financial_report`, `mis_builder`, `report_xlsx`,
`auditlog`, `auth_session_timeout`; only `date_range` and `queue_job` are LGPL-3), so
this introduces **no new licensing class**. `ncollection_account_assets` declares
LGPL-3, following the precedent of `ncollection_mis_templates` (LGPL-3, depends on
AGPL-3 `mis_builder`). **That precedent is inherited, not independently settled** —
whether an LGPL-3 wrapper over an AGPL-3 dependency is the right declaration is a
legal question this ticket did not answer, and it now applies to two modules rather
than one.

### Infrastructure touched by adding a repo

A new pin is not one file. `addons_path` is maintained by hand in **three** places —
`config/odoo.conf`, `config/odoo.prod.conf` and `.github/workflows/ci.yml` — and
nothing checks that they agree. All three were updated here; the missing guard is
filed separately rather than smuggled into this ticket.


## P2-T13 Subscription Payment (Stripe) — use Odoo core `payment_stripe` (no OCA/custom gateway)

- **OCA options evaluated**: `OCA/payment` provider modules. Odoo Community already ships a
  first-party, maintained **`payment_stripe`** provider plus the `payment` / `account_payment`
  framework (hosted checkout, signature-verified webhook, invoice reconciliation, tokenization).
- **Decision — configure Odoo core, build nothing.** DELIVERABLE_1 P2-T13 is explicit: *"Configure
  Odoo's built-in `payment_stripe` provider… do NOT build a gateway from scratch."* We add
  `payment_stripe` to `ncollection_billing`'s depends, configure the seeded provider in **TEST mode
  from env secrets**, and hook payment confirmation to extend the subscription
  (`payment.transaction._post_process` → `nc_subscription_id._nc_apply_payment`). No OCA dependency,
  no custom gateway. Signature/timestamp/amount are enforced by core; idempotency is ours.
- **Regional gateways (PayTabs, Tap)** for *tenant* invoices are **P6-T01** — they'll reuse this
  same Odoo payment-provider pattern, and OCA/community providers are re-evaluated there.

## P2-T14 Expiration & Dunning Scheduler — build custom (no OCA/Enterprise module)

- **OCA / core options evaluated**: subscription **dunning** (advance-warning + retry emails + timed
  expiry/suspension) is an **Odoo Enterprise** feature (`sale_subscription`) — not in Community — and
  `OCA/contract` models tenant-facing recurring contracts, not our SaaS platform lifecycle.
- **Decision — build a small admin-DB cron** on our own `ncollection.subscription`. A single daily
  `ir.cron` (`_cron_lifecycle_sweep`, clock-injectable for simulated tests) drives advance-warning
  emails, trial expiry, expiry after end_date+48h, suspension after the grace window, and the dunning
  reminder schedule — each idempotent via per-subscription trackers. It reuses the P2-T12 guarded
  transitions (suspension still projects to the tenant through the SaaS override) and the P2-T13
  payment surface (`payment_status`, portal pay link). No OCA dependency, no Enterprise module.
- **Auto-charge retry** (silently re-billing a stored card) is deferred — it needs saved payment
  tokens (not set up in P2-T13); the dunning here is the scheduled reminder-email cadence.
