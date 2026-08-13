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

## P6-T03 Support Ticketing — ADOPT `OCA/helpdesk`, build only the SLA gap

- **The doc named the candidate.** `DELIVERABLE_1_SYSTEM_DESIGN.md` lists
  `Helpdesk/Ticketing | OCA/helpdesk | P6-T03`, and its process is: search the 19.0
  branch → evaluate/install → pin if suitable → otherwise document why not. This entry is
  step 3 and step 4 at once: most of it is adopted, one piece is not available.
- **The 19.0 branch is real and maintained** — ten modules, Production/Stable. That is the
  decisive difference from the `auth_brute_force` precedent above, which was dead upstream
  on every branch ≥12.0 and therefore correctly rebuilt on Odoo core.
- **ADOPTED:** `helpdesk_mgmt` (ticket model, teams, stages, portal submit/track pages) and
  `helpdesk_mgmt_rating` (CSAT on close). Pinned at `a8c722a0` in `repos.yml`.
  - *Installs on the stock image* — `helpdesk_mgmt` depends on core `mail` + `portal` only
    and declares **no** `external_dependencies`, so unlike the `queue_job`/`openupgradelib`
    trap it needs no custom Dockerfile. Verified rather than assumed:
    `-i helpdesk_mgmt,helpdesk_mgmt_rating` on a clean DB exits 0 with zero tracebacks.
  - *No `website` dependency is dragged in.* The controllers carry `website=True`, which
    looked like a problem for a platform that is deliberately backend/portal-only (every
    NCollection route is `website=False`). Checked: the templates `t-call
    portal.portal_layout`, and both modules installed with `website` **uninstalled**.
  - *Odoo's own Helpdesk is Enterprise-only* — confirmed absent from the `odoo:19`
    Community image — so there is no core alternative to weigh this against.
- **BUILT CUSTOM:** SLA timers, in `ncollection_helpdesk`. `helpdesk_mgmt_sla` is **not on
  the 19.0 branch**; its migration PR #1012 is open and unmerged, and two earlier attempts
  (#1006, #1009) closed without landing. Vendoring an unmerged branch is the mistake
  `auth_brute_force` taught, so the timers are native: a policy model (per team+priority),
  response/resolution deadlines, and an hourly scan cron that refreshes the breach state —
  a stored state no ORM recompute can maintain, because only the clock changes it.
- **SUNSET note:** if PR #1012 merges, re-evaluate `ncollection_helpdesk` for retirement in
  favour of upstream. Keep the module's surface small so that stays cheap.
- **Portal isolation is adopted, not written.** `helpdesk_mgmt` already ships
  `partner_id child_of user.commercial_partner_id` — the same convention P6-T02 measured as
  this repo's majority pattern. We therefore add **no** `ir.rule`; instead
  `test_ticket_portal_isolation.py` pins that domain, so an OCA bump that weakens it fails a
  test rather than silently widening who can read a customer's tickets.
