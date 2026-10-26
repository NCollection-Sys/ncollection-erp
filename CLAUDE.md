# NCollection ERP — Project Context (read this first)

Auto-loaded every session. Stable facts + pointers; volatile detail lives in the
linked docs. To start a plan issue, run **`/solve-issue <number>`**.

## The team (who is who)
| Role | GitHub | Lane |
|------|--------|------|
| **DEV-1** | `omaressam7704` (Omar — repo owner, **the person you are usually talking to**) | Backend / Infra |
| DEV-2 | `aibrahimhlms` | Odoo / Logic |
| DEV-3 | `bakr33934-svg` | Frontend |

When suggesting next issues, prioritize the **`dev:DEV-1`** lane for Omar (he sometimes
drives other lanes with AI too, but DEV-1 is his own work).

## Communication style — for Omar (DEV-1) only
Omar's standing request: **respond like a caveman** — short blunt sentences, simple
words, caveman flavor ("Me run tests. Tests green. Fire good."). BUT this changes only
the wrapping, **never the substance**: still explain everything, include every check,
number, caveat, and risk; tables and evidence stay. Never sacrifice completeness for
the bit. (Teammates who use this repo get normal professional style.)

## What this is
A multi-tenant SaaS ERP for the GCC/UAE market, built on **Odoo 19 Community** with a
custom SaaS layer on top. Database-per-tenant. Repo: `NCollection-Sys/ncollection-erp`
(private, GitHub Free org — no enforced branch protection yet).

## The two "websites" (do not confuse them)
1. **The product** — Odoo 19 + `custom_addons/`, served at `localhost:8069`, real
   PostgreSQL. This is what ships.
2. **`demo/`** — a standalone React/Vite prototype at `localhost:5173`. Business screens
   use **mock data**; **login & signup are REAL** (they authenticate against the `ncollection`
   database via the Vite proxy). It's a design reference to be ported into Odoo OWL/QWeb
   over the Phase-1 tickets. Never deployed as-is.

## Runtime map
- `docker compose up -d` → two containers: `odoo` (`odoo:19`, port 8069) + `db`
  (`postgres:16`). DB data is in the `postgres_data` volume; filestore in `odoo_data`.
- Custom addons are **bind-mounted** `./custom_addons → /mnt/extra-addons` (live edits;
  apply with a module upgrade, no image rebuild).
- Dev config: `config/odoo.conf` (dev bootstrap — production config is ticket P1-T02).
- Postgres is exposed to the host on **port 5433** (for pgAdmin/DBeaver; 5432 avoided to
  not clash with a local Postgres). Odoo DB-manager master password (dev): `ncollection_dev`.
- The working database is **`ncollection`** (admin login `admin`/`admin`). `make bootstrap
  db=ncollection` creates it and installs our modules.

## Custom addons — **28**<!--count:custom_addons--> of them (count enforced; see #408)

This list said **four** until #408, and described two of them as empty. They are not:
`ncollection_core` is 8.6k lines and `ncollection_saas` is 9.9k. An agent told those were
"near-empty" reasons about them entirely wrongly, which is why the count is now enforced —
adding a module fails CI until someone updates this list.

**Platform layer** (never queries tenant ERP models directly — Rule 3):
- `ncollection_saas` — the provisioning engine: tenant lifecycle, provisioning jobs,
  config-sync (+ re-key), fleet migration, backups, checkout, domains, exchange rates, and **Tenant Administration**:
  the tenant form's Users & Access (list users, create, activate / deactivate, change roles, reset a password incl. the
  Tenant Administrator's) through the tenant's `ncollection.admin.service` over the same derived-bearer JSON-2 channel,
  authorised on every call (platform admin + the tenant's `platform_admin_ids` allow-list); the platform stores no password.
  Also **Delete Tenant** (Actions > Delete Tenant, typed identifier, same allow-list; database + filestore + platform
  records, never the platform DB or a reserved / protected name; audit `ncollection.tenant.deletion` outlives the tenant).
- `ncollection_subscription` — Tenant / Subscription / Plan / SaaS-admin dashboard.
- `ncollection_reseller` — partner reseller accounts, cascading branding, sub-tenants.
- `ncollection_billing` — subscription billing: invoices, UAE VAT, proration, Stripe.

**Tenant layer** (installed into each tenant database):
- `ncollection_core` — the security spine: 8 role groups + implications, license
  enforcement, menu visibility (Ring 1), workspace config, the financial gate, the
  aggregation engine, KPIs, anomaly detection, the customer dashboard. Also the **Workspace Administration Center**
  (Overview, Users, Roles & Permissions, Teams, Security, Company, Appearance, Document Stamps, System): `ncollection.role` =
  a named view of REAL `res.groups` (standard templates + custom roles that can never carry administration),
  `res.users.nc_role_ids` derived from the groups, password reset with forced change (`nc_must_change_password` +
  `/ncollection/password/change`), `ncollection.admin.service` for the platform, and the inline View endpoint
  `/ncollection/document/view/<model>/<id>`. Docs: `docs/markdown/WORKSPACE_AND_TENANT_ADMINISTRATION.md`.
- `ncollection_api` — the public REST API (#77): `/api/v1`, OAuth2
  client-credentials, scoped tokens, per-client rate limiting, metadata-only
  request logging. **Extends Odoo core's `res.users.apikeys`** rather than
  building a credential store — core already hashes the secret and hides it
  from the ORM (`_auto = False`). Tokens carry `scope='ncollection_api'`, which
  is what stops them being replayed as RPC credentials (core matches
  `scope IS NULL OR scope = 'rpc'`). `base_rest` is deprecated upstream and
  `installable: False` on 19.0; OCA `fastapi` needs five pip packages the stock
  image lacks — decision and revisit trigger in OCA_DEPENDENCIES.md.
- `ncollection_auth` — authentication audit log + hardened login/session defaults. Also the **administrative audit**:
  user / password / role events with target, detail and the platform administrator behind them, never a secret; the
  Security screen of the Workspace Administration Center reads it.
- `ncollection_audit` — audit-trail **infrastructure** on OCA `auditlog` (#81):
  the client IP `auditlog` has no field for, audit rules seeded as data and
  re-seeded on every registry load (R-014), a retention period that is a system
  parameter rather than a hardcoded 180, and tamper evidence — a per-row content
  digest plus hourly range seals, with `perm_unlink` taken off the manager group.
  **Infrastructure only, and #81 was RESCOPED**: three of the four models
  P8-T05 names cannot be audited with this `auditlog`, each measured —
  `res.users` disables the plan seat limit (#428), `res.partner` breaks partner
  creation via ThrowAwayCache (#429), `account.move`/`account.move.line`
  recurse ~950 frames deep (#431). The last is the model the acceptance
  criterion needs, so **the invoice-amount criterion is not met**; what ships is
  `sale.order` plus the `ncollection.*` models. Financial audit UX remains #124.
  Group-restricted fields and `ncollection.auth.log` are never logged —
  `auditlog` ignores
  `groups=` and its log table is readable by a lower-trust group.
- `ncollection_branding` — **System Branding** (ONE record: the system's name, favicon, system / login / administration logos, browser title, copyright text and primary / secondary
  colours in the existing `--nc-color-*` tokens, resolved by `nc_brand()` for every surface, changed ONLY by the `System Branding Manager` group (platform staff and a Platform Administrator inside a tenant; the tenant Owner cannot); never the tenant's colours or the company's identity — three identities, three places;
  the persisted names stay `ncollection.product.branding` — docs: `docs/markdown/SYSTEM_BRANDING.md`), theme, logo, colors, and the document layer: the NCollection print layout (default), document types,
  and **Document Stamp templates** (mm geometry, document types, paper, priority; PDF overlay; OWL designer with a real-PDF
  preview; the stamp image is group-restricted to **Document Stamp Manager**, also reachable from Accounting > Configuration > Document Stamps). Docs: `docs/markdown/DOCUMENT_PRINTING_AND_STAMPS.md`.
- `ncollection_ai` — tenant-side AI: gateway client, context injection, PII redaction.
- `ncollection_approvals` — configurable approval workflows.
- `ncollection_data_import` — onboarding import toolkit (CSV templates + wizard).
- `ncollection_sale_attachments` — the "Files" paperclip column in the Quotations / Sales Orders lists. The paperclip,
  its ONE modal viewer (Odoo's own FileViewer, extended: position "2 / 5", fallback card, RTL) and the count/metadata
  (`nc.attachment.mixin`) live in `ncollection_core`, shared with invoices / bills / purchase orders / payments.
  No model, no ACL, no route: it reads `ir.attachment` as the user. `auto_install` with `sale`.
- `ncollection_sale_documents` — Sales opens on **Invoices** (Odoo's own customer-invoice action, first in the menu: New is
  a customer invoice, no quotation or order needed). It also switches on the Sales root menu: Odoo 19 leaves it inactive
  unless `sale_management` is installed, and plans list `sale`, so without this there is no Sales app to open, **Duplicate** (نسخة مماثلة), and **View / Preview that is the very PDF
  Print produces** (`nc.document.print.mixin`, ncollection_core: one report + one context for both). `auto_install` with `sale`.
- `ncollection_purchase_documents` — the same for Purchase: **Vendor Bills** first, Duplicate, Files, and View / Preview =
  the printed RFQ or purchase order. `auto_install` with `purchase`.
- `ncollection_stock_barcode` — the warehouse barcode workflow Community lacks (`stock_barcode` is Enterprise, the OCA
  `stock_barcodes` port is not merged on 19.0): scan receipts / deliveries / transfers (products, lots, serials,
  locations), correct a mis-scan, validate with the stock engine's own `button_validate`, and count inventory with its
  own `action_apply_inventory`. Inside the Inventory app (no new tile); every call runs as the user, no sudo.
  `auto_install` with `stock`. Decision record: `docs/markdown/APPLICATIONS_AND_BARCODE.md`.
- `ncollection_helpdesk` — SLA response/resolution timers on top of OCA
  `helpdesk_mgmt` (+ `helpdesk_mgmt_rating`), which is what P6-T03 adopts for the
  ticket itself. Also carries the ownership guard on OCA's `/ticket/close`, whose
  `.sudo()` lookup of a request-supplied id bypassed the portal record rule.

**Financial** (`ncollection_account_*` + MIS):
- `ncollection_account_core` — shared financial base, subscription restriction hooks. Also the invoice / bill
  **payment status** (Unpaid / Partially Paid / Overdue / Paid in Full / Overpaid — computed from residuals, due dates and
  reconciliations, never stored) and **payment allocation**: the payment form's Confirm posts natively, then applies the
  payment through Odoo's own `js_assign_outstanding_line` (= `reconcile()`), selected document first, then earliest due;
  the rest stays an outstanding credit. No parallel invoice / payment / reconciliation engine. Print Receipt / View = one report.
- `ncollection_account_reports` — native report engine: filters, drill-down, PDF + XLSX. Also the **Reports hub**
  (7 categories; native Odoo analysis menus are re-filed into it with the group that already holds the ORM right),
  **Tax Analysis**, and the hardening of the OCA report wizards (restricted to accounting read-only on every registry
  load). Capability audit: `docs/markdown/ACCOUNTING_REPORTING_CAPABILITY_MATRIX.md`.
- `ncollection_account_reports_oca` — the OCA-engine ADAPTER (pilot: Trial Balance). Runs OCA's
  `account_financial_report` and maps it onto the NCollection report contract, so the NCollection
  viewer/PDF/XLSX/drill-down are unchanged and no OCA screen is shown. Off by default (a system
  parameter selects the source); see the ADR amendment in `docs/markdown/OCA_DEPENDENCIES.md`.
- `ncollection_account_dashboard` — Finance / Accountant / Cash / CEO dashboards
  (presentation only — consumes the report services).
- `ncollection_account_analytics` — cost/profit centres on Odoo's own analytic
  plans (no dimension model of ours), the three FINANCIAL KPIs `ncollection_core`
  deliberately declined (Revenue Growth %, DSO, Gross Margin %), budget variance
  and trend extrapolation. ADR #15 names it the native owner of financial
  computation alongside `ncollection_account_reports`.
- `ncollection_account_budget` — budget planning, revision and approval (an
  approved budget stops accepting line edits; a revision supersedes rather than
  overwrites), plus the Budget-vs-Actual report. Budget lines key off a GL
  account + `analytic_distribution`, the same way the actuals do, so the two
  sides compare like with like. Satisfies the `_nc_variance_payload` contract
  `ncollection_account_analytics` has been calling since #120.
- `ncollection_account_assets` — fixed assets. **The one module that ADOPTS an OCA
  engine rather than building native**: `account_asset_management` (pinned #122) owns
  depreciation boards and posting, because Odoo 19 Community ships no assets at all and
  OCA's model uses the same `analytic_distribution` #120 established — the single-FK
  problem that ruled OCA out for #121 does not reproduce. This module owns what OCA
  lacks: the **account-type guard** (a profile pointed at a plain `expense` account
  leaves #114's cash flow balanced and reconciling while its Operating/Investing split
  is silently wrong), the **transfer** flow, and the **Asset Register** on the F2 engine.
- `ncollection_cheque_print` — prints the variable data of an `account.payment` onto a PRE-PRINTED bank cheque. Templates are
  data (`ncollection.cheque.template` + fields in millimetres; Mashreq NeoBiz / ADCB / FAB ship as records, a fourth bank
  needs no Python), a drag-and-drop designer (OWL view widget), an exact-size reportlab PDF (no A4 wrapper, no browser
  scaling; pure-Python Arabic shaping), printer calibration, and an append-only print-job audit (reprint / void need a
  reason). Never generates MICR, logos or security elements. Shared words service lives in `ncollection_account_core`
  (`ncollection.amount.words`). Shipped sizes are PROVISIONAL (`dimensions_confirmed=False`) until measured on paper.
  **A companion module**: entitled exactly when its tenant has Accounting (`ncollection_subscription/models/companions.py`,
  applied in the one authority `_nc_effective_module_list`); never named in a plan, never in the plan picker.
  Docs: `docs/markdown/CHEQUE_PRINTING.md`.
- `ncollection_account_localization_uae` — TRN validation + FTA compliance tracking.
- `ncollection_mis_templates` — ready-made Balance Sheet and P&L MIS templates.

**Demo:** `ncollection_demo_freshorigin` — the Fresh Origin showcase dataset.

In Odoo, a feature = **one module** holding both backend (`models/*.py`, `controllers/`)
and frontend (`views/*.xml`, `static/src/*.js` OWL, `*.scss`). There is no separate
frontend/backend deploy — it's a monolith.

## How we work
- **Branches:** `main` = stable, `develop` = active integration. Feature work branches off
  `develop`. **They are NOT in sync** — `develop` is hundreds of commits ahead, and `main`
  is the repo's **default** branch. Two consequences that bite silently:
  1. **A new workflow added on `develop` never runs.** GitHub registers workflows from the
     DEFAULT branch, so a `.github/workflows/*.yml` file that exists only on `develop` is
     inert — it will not appear in the Actions list and no event will trigger it. Verified:
     `e2e.yml` is registered and does *not* exist on develop; `close-linked-issues.yml`
     exists on develop and is *not* registered. `nightly.yml` documents the same rule for
     schedules.
  2. **`Closes #<n>` never fires**, because closing keywords are honoured only on a merge
     into the default branch. Close issues by hand — `/solve-issue` Phase 7 does this, and
     it is why issues appeared to close automatically when that command was used.
- **Per issue:** `/solve-issue <n>` → it checks the issue is open, in order, and its
  dependency issues are closed → branch `feature/<n>-<task-id>` off `develop` → PR to
  `develop` with **`Closes #<n>`** → CI → 1 review → merge.
- **Convention: a CLOSED issue = a COMPLETED task.** This is what makes dependency checks
  work, so only close an issue when its work is truly merged.
- **CI** (`.github/workflows/ci.yml`, runs on PRs): `lint` (flake8 + pylint-odoo baseline),
  `architecture-guard` (`scripts/ci/architecture_guard.py`), `test` (installs addons +
  `--test-tags` scoped to ours), `build` (compose smoke test). No CD yet (Phase 2/3).
- **The GitHub issues are the plan** (`[P<phase>-T<nn>]` / `[F<n>-T<nn>]` task IDs, phase +
  dev labels) — ~122 task-ID issues as of 2026-08-13, up from the 100 this line used to
  claim. Deliberately NOT machine-checked: it needs the GitHub API, and a lint-job guard
  should not make network calls, so it is dated rather than enforced.
  Phase-9 (marketplace) is deferred until after Phase 10.

## Standing rules (binding — full list in docs/markdown/TASK_PROMPT_TEMPLATE.md)
1. Odoo 19 views: `<list>` not `<tree>`; no `attrs=`.
2. Never modify Odoo core; extend via addons.
3. Two-layer separation: platform addons (`ncollection_saas`, `ncollection_subscription`)
   must not directly query tenant ERP models — go through RPC.
4. Any UI restriction (menu/`groups=`) must be mirrored at the ORM/RPC layer.
5. OCA-first for mature infrastructure and security concerns.
  Business features that become part of the NCollection product should gradually migrate to native ncollection_* modules according to the project roadmap.
  Never introduce a new OCA dependency without checking the project architecture first.
6. Small incremental commits, each verified. Run `make hooks-install` **once** and the
   pre-push hook runs the fast gates for you — **11**<!--count:prepush_gates--> of them,
   not the four this line claimed before #394 and not the nine it claimed after: flake8 ·
   shellcheck · `invariants-tests` · `invariants` · `arch-guard-tests` · `skip-gate-tests` ·
   `role-matrix-tests` · `role-matrix` · `doc-counts-tests` · `doc-counts` ·
   `architecture-guard` (plus `pylint-odoo`, which fails only on findings beyond its
   baseline). That number is now **enforced** by `scripts/ci/check_doc_counts.py` — it went
   stale within hours twice, because nothing checked it (#408).
   Each guard's **tests run before the guard itself**: a scan that reports "clean"
   while its own rules are broken is worse than no scan, and this repo has shipped
   three of those (#330, #348, #311). Add `cd demo && npx tsc --noEmit` if `demo/`
   changed.
7. No secrets in git; dev creds live in `.env` (gitignored; template `.env.example`).
8. The architecture documents are authoritative.
9. **Postgres CLI tools need an explicit `-d`.** `psql` and `pg_isready` default the target
   database to the *username* — the role here is `odoo` and no such database exists, so a
   missing `-d` fails silently-ish with `FATAL: database "odoo" does not exist`. This
   disabled the routing suite's idempotency for weeks (REGRESSIONS.md R-002).
   `dropdb`/`createdb` are fine without it — they default to the `postgres` maintenance db.
10. **Never `|| true` on a state-changing step you later depend on.** Fail loud with an
    actionable message. A swallowed restart failure once printed "✅ ready" over a stale
    cache (R-005).
11. **Derive container IDs** via `docker compose ps -q <service>`; never hardcode
    `ncollection-*` names — they break under a non-default `COMPOSE_PROJECT_NAME` (R-006).
12. **Verification scripts must be idempotent *and prove it*** — run twice; the second run
    must be a no-op. Claiming idempotency in an echo is not evidence (R-002).
13. **Before merging, run `make verify-all`** — routing + provisioning + e2e — not just the
    suite for your own lane. A ticket that proves only its own lane cannot see a cross-suite
    regression, which is exactly how breakage stayed invisible.
14. **Never mutate the shared dev stack while another agent may depend on it.** Background
    agents share ONE Docker stack and ONE bind-mounted working tree — no isolation. An ad
    hoc `docker compose up/down/restart` (outside a suite's own documented flow, e.g.
    `e2e-verify`'s load-bearing odoo/nginx restart) or deliberately breaking a bind-mounted
    file for a RED proof can land mid-flight under a *different*, concurrently-running
    agent and produce a **false CRITICAL indistinguishable from a real one** — it already
    has, twice, same day (R-018: a fabricated tenant-isolation breach, a phantom
    provisioning error, neither reproduced on a clean re-run). Do such work **before**
    fanning out background reviewers/`verify-runner`, never during. No agent may "fix" a
    stack it doesn't own by restarting it — if it looks down or flaky, run
    `scripts/dev/stack_settled.sh` and report what you see instead of guessing. Before
    trusting a scary/CRITICAL finding, re-run whatever produced it once — R-018's false
    positives both cleared on the very first retry.
If a requested implementation appears to conflict with
DELIVERABLE_1_SYSTEM_DESIGN.md,
ARCHITECTURE_DATA_PLATFORM.md,
or ARCHITECTURE_SECURITY.md,
STOP and ask before changing the architecture.

## Odoo 19 gotchas (live-verified here — save yourself the debugging)
- HTTP JSON routes are `type='jsonrpc'` (`type='json'` is deprecated).
- `res.users` groups field is `group_ids` (was `groups_id`); `base.default_user` template
  was removed — create users with an explicit `group_ids` set.
- **`auth='none'` routes default to a READ-ONLY transaction.** `http.py`:
  `default_mode = routing.get('readonly', default_auth == 'none')`. Any write
  from such a route dies with `psycopg2.errors.ReadOnlySqlTransaction` — and if
  you catch it, the response still looks perfect while nothing is persisted.
  Pass `readonly=False` explicitly (#77).
- Sending the `X-Odoo-Database` header on `/web/session/authenticate` makes Odoo skip the
  session cookie — only send it on session-less public calls (e.g. signup).

## Make cheat-sheet (`make help` for all)
`up` `down` `restart` `logs` `ps` · `bootstrap db=<db>` · `install m=<mod> db=<db>` ·
`upgrade m=<mod> db=<db>` · `psql db=<db>` · `shell` · `demo` (runs the React app).
**First run:** `make hooks-install` (pre-push gates) · `make doctor` (diagnose the env) · `make pdf-hosts` (after every
odoo container restart — see the PDF note below).
**Tests:** `make test` runs the SAME suite CI runs, locally (`m=<module>` to scope). Its
module list is derived from `ci.yml` by `scripts/dev/ci_matrix.py`, never copied, so local
and CI cannot drift. It owns `nctest` and drops it at both ends.
**Before merging:** `make verify-all` (**8**<!--count:verify_all_suites--> suites: routing + provisioning + config-sync + cron ×2
+ financial-bootstrap + **upgrade** + e2e).

## Test fixture ownership (do not cross the streams)
Each suite owns a database namespace and may **only** drop its own. These used to be
shared, so running one suite silently destroyed another's fixtures (REGRESSIONS.md R-004).

| Suite | Owns | Cleanup |
|---|---|---|
| Routing proof (P1-T06) | `rtclienta` · `rtclientb` · `rtadmin` | `make routing-clean` |
| E2E (P1-T20) | `e2eclienta` · `e2eclientb` · `e2eadmin` | `make e2e-clean` |
| Provisioning (P2-T01) | `prov*` | — |
| Load test (P3-T03) | `loadtesta` · `loadtestb` · `loadtestc` | `make load-test-clean` |
| Financial bootstrap (P3-T01) | `fintest` | self-drops (start + end of run) |
| Cron starvation (#310) | `cronstall` — **on its own private Postgres**, not the shared `db` | `make cron-starvation-clean` |
| Cron scope (#343) | `cronscopeplatform` · `cronscopetenant` — **also on their own private Postgres** | `make cron-scope-clean` |
| Platform DB (provisioning + config-sync run *against* it) | `saastest` · `ncplatform` | none — **persistent, do not drop** |
| Demo tenant (`make demo-tenant`) | `albarari` | `make demo-clean` |
| Aggregation bench | `aggbench` | — |
| Local test suite (`make test`) | `nctest` | self-drops (start + end of run) |
| Upgrade proof (#362, #381) + drift arm (R-037) + cron arm (R-039) | `upgrgreen` · `upgrred` · `upgrcore` · `upgrdrift` · `upgrcronnew` | self-drops (start + end) · `make upgrade-clean` |
| Upgrade rehearsal (R-037) | `upgrreh<source>` — a scratch COPY of the database being rehearsed | self-drops on every exit path |
| Platform release proof (`make upgrade-platform-verify`) | `upgrplat` — a scratch COPY of the platform database | self-drops on every exit path · `make upgrade-clean` |

The last three rows are **not** throwaway fixtures. `saastest` in particular is the
default `PLATFORM_DB` that `verify_provisioning.sh` and `verify_config_sync.sh` run
*against* (they create and drop `prov*` tenants underneath it) — dropping it breaks
both suites until it is rebuilt. `make orphan-dbs` lists everything with no owner in
this table; keep the two in step, or the list starts naming live fixtures and people
learn to ignore it.

The cron-starvation and cron-scope harnesses are the suites that do **not** put their
fixtures on the shared `db`, and the reason generalises: the dev `odoo` container runs with no `-d`, and Odoo's
`cron_database_list()` falls back to `list_dbs(True)` — so it runs the crons of **every database
on that server**, including every leftover fixture. Any timing measured against a shared-server
database is therefore attributable to nobody. See `DESIGN_CRON_AND_QUEUE_TOPOLOGY.md` §5.1. The cron-scope harness has a second
reason on top of that one: its RED arm *deliberately* runs every database's crons, so
pointing it at the shared server would mutate other suites' fixtures on purpose.

Fixture names must be **alphanumeric**: `db_filter=^%d$` routes a subdomain to the database
of the same name, underscores are invalid in hostnames, hyphens need Postgres quoting.
So tenant key === subdomain === database name, always.

## What a green check does NOT mean
- **`architecture-guard`** checks secrets on every changed file and XML on every `.xml`, but
  its two-layer/Odoo-syntax rules are scoped to `custom_addons/`. Infra surfaces (shell,
  compose, workflows) are covered by `scripts/ci/invariants.py` instead. It also reports
  `0 file(s)` legitimately on a local pre-commit run — untracked files are not in `git diff`.
- **CI cannot block a merge here.** Branch protection is unavailable on GitHub Free private
  repos (verified: HTTP 403), so a red PR is merge-able. `canary.yml` re-verifies `develop`
  after every merge and files a `broken-develop` issue — that is **detection, not a gate**.
  Treat such an issue as top priority. See `docs/markdown/BRANCH_PROTECTION.md`.

- **A suite that "passed" may have measured nothing.** Odoo exits **0** having failed to
  load a module, two ways: a missing dependency (ERROR `Some modules are not loaded`), and an
  unknown module name or `installable: False` (**WARNING only**, then `Modules loaded.`). A
  harness that checks only the exit status then measures something that cannot happen and
  blames whatever it was testing — that is how 120s of phantom cron starvation was nearly
  filed as a regression against a healthy `develop`. Every setup step now pipes its log
  through `scripts/dev/assert_odoo_setup.sh`, which refuses instead (#385).
- **`./oca` is generated and gitignored, so a fresh `git worktree` has none** — and docker
  creates a missing bind-mount source as an **empty directory** rather than failing, so Odoo
  starts fine while every OCA-dependent module silently fails to install. `make up`,
  `verify-all`, `cron-starvation-verify`, `routing-up` and `staging-build` refuse via
  `scripts/dev/assert_oca_present.sh`. Run `make oca` first in a new worktree (#384).
- **Those setup markers are database-wide, not per-module.** Odoo builds its graph from every
  installed module on the database, so an unrelated broken row makes the check blame the
  module you are upgrading. `scripts/dev/assert_modules_settled.sh` runs first on the
  persistent platform DBs and names the real culprit (#388).
- **A report PDF that "works" may be unstyled, and "Arbitrary Uncaught Python Exception" may not be Python.** wkhtmltopdf runs
  *inside* the odoo container and loads the stylesheet/letterhead from `<tenant>.localhost`; the container cannot resolve it, so
  Odoo returns a PDF with exit 0 and a log line `HostNotFoundError` — no letterhead, no borders, different pagination (`make
  pdf-hosts`; `make doctor` warns). Separately, a Windows download manager (IDM) answers `POST /report/download` with an empty
  `204 Intercepted by the IDM Advanced Integration`; Odoo's client then prints that exact message though the server returned a
  valid PDF. Read the network response and the Odoo log before assuming a traceback.
- **A visible menu is not an authorised report, and a hidden menu is not a control.** A role audit found the Sales / Purchases /
  Inventory hub categories empty for *every* role (native menus gated on Administrator groups) and the OCA ledger wizards open
  to any internal user. Test each role server-side (`nc_report_run`, direct `create`, the PDF/XLSX routes), never as `admin`:
  a superuser holds every group and passes every check.
- **Language and config set by raw SQL are invisible to a running server** (`res.users.context_get`, `ir.config_parameter` are
  cached). Change them through the ORM / RPC, or the next request still sees the old value.
- **A skipped Odoo test counts as a passing one** (`0 failed, 0 error(s) of N tests`).
  `scripts/ci/check_skips.py` fails CI on any skip not in `expected_skips.txt`, keyed on test
  identity rather than reason text — allowlisting a reason string would allowlist the
  dangerous case (#363).
- **`docs/ROLE_MATRIX.md` is enforced by `scripts/ci/check_role_matrix.py`, not by the Odoo
  tests** — those compare two Python copies to each other and never open the file. Only **§2**
  is machine-checked; §3-§5 are unverified prose (#382).

## Docs index (`docs/markdown/`, PDFs in `docs/pdf/`)
- `LOCAL_DEV_AND_ARCHITECTURE.md` — onboarding + runtime + workflow (start here for setup).
- `DELIVERABLE_1_SYSTEM_DESIGN.md` — the 100 tasks (scope, deps, acceptance) + §8 exec order.
- `SPRINT_SCHEDULE.md` — parallelization / sprint grid.
- `ARCHITECTURE_DATA_PLATFORM.md` · `ARCHITECTURE_SECURITY.md` — backend & security deep-dives.
- `TASK_PROMPT_TEMPLATE.md` — canonical Standing Rules + manual issue template.
- `WORKSPACE_AND_TENANT_ADMINISTRATION.md` — roles = real groups, password reset, the platform's user-administration service, audit, boundaries.
- `DEPLOYMENT_DOMAIN_CONTRACT.md` — the production domain (`balancecloud.ae` + `*.balancecloud.ae`): the one `DOMAIN` setting, the wildcard route, DNS / TLS / forwarded headers / trusted proxy, what is proven where.
- `RUNBOOK_RELEASE_UPGRADE.md` — deploying code to a database that exists: backup, deploy, preflight, rehearse on a copy, targeted upgrade, smoke, traffic. Never `-u all`.
- `RELEASE_COMPATIBILITY_GATE.md` — why a database the code is ahead of answers a branded 503 instead of an HTTP 500: the gate before the registry (`ncollection_core/release_gate.py`, server-wide), the states, holds, the platform's status channel, provisioning `validating`, Company Entry, Fleet Migration (platform first, enforced), and how an
  operator reads a blocked PLATFORM database (`make release-status db=ncollection`).
- `SYSTEM_BRANDING.md` — system branding vs tenant branding vs company identity, the one resolver, favicon / logo validation, where "Odoo" was removed and what stays.
- `DOCUMENT_PRINTING_AND_STAMPS.md` — the one print pipeline, View = Print, the NCollection layout, the stamp system, how to prove a PDF.
- `BRANCH_PROTECTION.md` — required CI checks + 1-approval policy, and why none of it is
  **enforceable** on the current GitHub plan (verified 403) — read before assuming CI blocks.
- `DEMO_TENANT.md` — the populated **Al Barari Trading** workspace (`make demo-tenant`):
  what it seeds, which login shows which role, and how to rebuild it.
- `REGRESSIONS.md` — the regression ledger: symptom → root cause → the guard that now
  prevents recurrence. **A regression is not closed until a guard exists.**
- `TESTING_STRATEGY.md` — what we test at each layer, what a green result actually proves,
  the ranked gap register (G1–G11, each with its owning stack + DB prefix), and the measured
  `958d8d6` baseline (17/17 green, 10m37s warm). Read before adding any new suite.
- `DESIGN_CRON_AND_QUEUE_TOPOLOGY.md` — where work that can block on someone else's server is
  allowed to run (#310): why outbound work is queued rather than croned, why `root` capacity had
  to change, and why a `max_cron_threads` edit to `odoo.prod.conf` is a no-op.
- `PRD.md` · `DELIVERABLE_2_TIMELINE_AND_TOOLING.md` · `PLANNING_REVIEW.md` — product & planning.

Architecture priority (highest first)

1. DELIVERABLE_1_SYSTEM_DESIGN.md
2. ARCHITECTURE_DATA_PLATFORM.md
3. ARCHITECTURE_SECURITY.md
4. DELIVERABLE_2_TIMELINE_AND_TOOLING.md
5. SPRINT_SCHEDULE.md

If older documents contradict these, treat them as historical only.

## Architecture Safety

Never:

- redesign the architecture without approval
- add new OCA modules without approval
- modify module states or databases unless explicitly requested
- replace existing code when extending it is sufficient

When unsure:
STOP.
Explain.
Ask.

## AI Expectations
Think like a senior software architect.

Do not optimize for writing code quickly.

Optimize for:

- maintainability
- scalability
- security
- backward compatibility
- minimal scope
- long-term architecture

Always preserve existing architectural decisions unless explicitly instructed otherwise.

## AI agents (auto-invoked — the user never names them)

Project subagents live in `.claude/agents/`. The orchestrator invokes them
**proactively and automatically**, without being asked, at these triggers:

| Trigger | Auto-run (in parallel) |
|---|---|
| After editing `custom_addons/**`, before any PR | `code-reviewer` + `odoo-reviewer` + `tenant-isolation-auditor` (+ `security-reviewer` when auth / user input / secrets / payments are touched) |
| Before every merge (Rule 13) | `verify-runner` (cross-suite gate, background) |
| Any OCA / new-dependency decision | `oca-scout` (REUSE / ADOPT / BUILD) |
| After a CI failure, a fixed regression, or at session wrap-up | `self-improver` (proposes harness improvements as a PR) |

Address every CRITICAL/HIGH finding before pushing. For a large or high-risk diff,
prefer `/code-review ultra`.

**Shared-stack guardrail (binding, Rule 14 / R-018):** background agents share one
Docker stack and one bind-mounted tree — there is no isolation between them. Never
restart/recreate the stack, or deliberately break a bind-mounted file, while other
agents may be relying on it; do that before fanning out, not during. Any agent can
sanity-check with `scripts/dev/stack_settled.sh` before trusting a scary finding.

**Self-improvement guardrail (binding):** every agent — `self-improver` included —
**PROPOSES via a PR and NEVER auto-merges.** Branch protection is unavailable on this
GitHub plan (403), so the `canary` + human review are the only safety net; an agent
that merged its own work would remove it. **Workers advise; the human merges.**
