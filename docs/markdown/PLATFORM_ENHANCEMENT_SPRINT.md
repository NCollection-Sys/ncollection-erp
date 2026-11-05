# Platform enhancement sprint — what changed, what it means, how to release it

Branch `feature/platform-enhancement-sprint` (base `5decb87`). Nothing here is deployed; the shared runtime is untouched.

## 1. Settlement (Paid Amount, Balance Due, Overpaid)

Five different things are kept apart, because mixing them is how a report lies:

| Thing | Where it lives | Changed by this sprint |
|---|---|---|
| Native amount due | `account.move.amount_residual` | never |
| Payments reconciled to the document | `account.partial.reconcile` | never |
| **Invoice-attributable excess** | `nc_overpaid_amount` (stored) | new |
| Unallocated customer credit | the open (unreconciled) line of the payment | never; shown on the payment (`nc_is_overpaid`) and on the dashboard (Customer Credit) |
| Refund / credit-note balance | the credit note's own residual | never |

**A negative Balance Due is shown only with evidence.** An excess is attributed to an invoice only when (1) the payment was
**recorded against that invoice** with Odoo's Register Payment (the wizard stamps `account.payment.nc_registered_move_ids`),
(2) the payment line settled **nothing else**, and (3) the document's own residual is zero. An advance that a user later assigns
to an invoice with the outstanding-credits widget, a bank import, a payment of an earlier release, a payment spread over several
invoices, and a credit-note remainder are all **customer credit**, not the invoice's excess: the invoice reads Paid in Full /
Balance Due 0 and the credit is shown once, on the payment. `Paid Amount + Balance Due = total` always.

Lifecycle, measured on Odoo 19 (`TestSettlementExcess`): **resetting a payment to draft does not remove its reconciliation** — the
invoice stays paid and the excess stays until the reconciliation is removed (`remove_move_reconcile`), at which point the stored
excess recomputes to 0 and the native residual returns. Reversal by credit note makes the invoice `reversed`: no status, no excess.

Only `nc_overpaid_amount` is stored; Paid Amount, Balance Due and Settlement Status are computed and **sortable in SQL**
(`_order_field_to_sql`), so ordering is the database's, over the whole result set, with record rules applied.

Taxable / Non-taxable is decided by the **tax group** of the lines (matched on the stable external-id suffix, never the translated
name): standard and zero-rated are taxable; exempt, out of scope and untaxed are not. A zero VAT amount alone decides nothing.

## 2. Release requirements (not executed — needs separate authorisation)

* Modules: `ncollection_core`, `ncollection_account_core`, `ncollection_account_dashboard` (and the manifests bumped for them).
* New stored column `account_move.nc_overpaid_amount` and the table `nc_payment_registered_move_rel` are created by the module
  upgrade. **Backfill:** the column computes on upgrade from existing reconciliations; because the stamp did not exist before,
  every historical payment has no evidence, so **every existing document backfills to 0** — no historical overpayment is invented.
  Historical remainders appear as customer credit.
* New Arabic terms are NOT loaded by a targeted `-u`: run `mods._update_translations(['ar_001'])` for the touched modules, then
  `env.registry.clear_all_caches(); env.registry.signal_changes()` (see the 2026-11-04 release notes).
* nginx (separate approval): the committed `ncollection.dev.conf` / `ncollection.prod.conf` re-resolve the `odoo` upstream
  (`resolver 127.0.0.11 valid=10s ipv6=off;` + `zone` + `server … resolve;`, nginx >= 1.27.3; the image is 1.27.5). Deploy =
  `nginx -t` then `nginx -s reload`. Proof: `scripts/dev/nginx_upstream_reresolve_test.sh` (private network; the old config FAILs,
  the new one PASSes).
* Rollback: restore the pre-release dump of each database (the release procedure's backup volume) and the previous source tree.

## 3. Dashboard accuracy release (DASH-02) — database × module × version × migration

Evidence for every KPI: `DASHBOARD_ACCEPTANCE_MATRIX.md`. This section is only what an operator must do. **Nothing here was executed
on a shared database**; every row below was rehearsed on a scratch copy (`pg_dump | pg_restore`, source read-only) or a disposable fixture.

| Database (role) | Module | Current | Target | Migration |
|---|---|---|---|---|
| `saastest` (platform) | `ncollection_core` | 19.0.1.27.0 | 19.0.1.28.0 | `-u`: new service model, wizard override, ACL rows, translations; no schema change on this DB |
| | `ncollection_subscription` | 19.0.1.18.0 | 19.0.1.19.0 | `-u`: code only (MRR in one currency, "expiring soon") |
| | `ncollection_dashboard_export` | — | 19.0.1.1.0 | **`-i`** (an `auto_install` module is NOT installed by `-u` on an existing database) |
| `ncollection` (accounting tenant and platform modules) | `ncollection_core` | 19.0.1.27.0 | 19.0.1.28.0 | `-u` |
| | `ncollection_account_core` | 19.0.1.12.0 | 19.0.1.13.0 | `-u`: new stored `account_move.nc_overpaid_amount` + table `nc_payment_registered_move_rel`; **backfill 0** |
| | `ncollection_account_reports` | 19.0.1.12.0 | 19.0.1.13.0 | `-u`: new abstract model (open items), Financial Summary figures |
| | `ncollection_account_dashboard` | 19.0.1.3.0 | 19.0.1.4.0 | `-u`: Customer Credit / Vendor Prepayments cards |
| | `ncollection_subscription` | 19.0.1.18.0 | 19.0.1.19.0 | `-u` (installed on this database too: omitting it leaves a SCHEMA DRIFT error, found by the rehearsal) |
| | `ncollection_dashboard_export` | — | 19.0.1.1.0 | `-i` |
| `e2euae` (UAE e-invoicing tenant, **protected**) | `ncollection_core` | 19.0.1.27.0 | 19.0.1.28.0 | `-u` |
| | `ncollection_account_core` | 19.0.1.12.0 | 19.0.1.13.0 | `-u` (column + table, backfill 0) |
| | `ncollection_dashboard_export` | — | 19.0.1.1.0 | `-i` |
| `einvfull`, `einvdev` | as `e2euae` | | | not rehearsed separately (same module set, no data) |
| `atlas`, `ncollection_demo`, `awtar` | | `awtar` is **behind the runtime** (core 1.24, account_core 1.9, reports 1.10): it needs the earlier releases first | | not in this release's scope |

Order: backup every database → deploy code → `release status` shows each database `UPGRADE_REQUIRED` (branded 503, by design) →
per database `-u <its modules> -i ncollection_dashboard_export` (never `-u all`) → `_update_translations(['ar_001'])` for the touched
modules + `clear_all_caches` → smoke. New Arabic terms are not loaded by `-u`.

**Rehearsal results** (disposable fixture built on the runtime tree 5decb87 with invoices, a 1,000 payment on an 800 invoice, a
partial payment, a credit note, a vendor bill and an advance to the supplier; then the sprint tree):

* every database upgraded with exit 0 and no error; a **second** `-u` was a no-op (same versions, same figures);
* **existing documents backfill to `nc_overpaid_amount = 0`** (0 non-zero rows on all copies); native `amount_residual` is untouched
  (`909.3` on the accounting copy before and after; `210.0` on the UAE copy);
* the Finance dashboard on the fixture: Receivables 280 → **600** (owed) + Customer Credit **320**; Payables −310 → **400** + Vendor
  Prepayments **90**; Revenue / Net profit / Assets / Liabilities unchanged;
* the UAE copy: the three e-invoice rows (uuid, XML SHA-256, states, write date) are identical to the live database; no submission.

Rollback: restore the pre-release dump of each database and the previous source tree (the release procedure's backup volume).

## 4. Dashboard UX and currency — execution tracker (DASH-UX)

| ID | Pri | Observation / root cause | Dashboards | Status | Automated evidence | Browser evidence | Remaining risk |
|---|---|---|---|---|---|---|---|
| DASH-UX-01 | P0 | User saw `$491,617` on a Sales dashboard of an AED company. **Root cause: it is Odoo's built-in SAMPLE data** (`sales_sample_dashboard.json`, `$` baked into the sample) shown because the company has no sales; reproduced exactly (189 / 456 / $491,617 / $6,828, `$` axis) on an empty AED company. Real dashboards print bare numbers (no symbol) because Odoo's cards use `FORMAT.LARGE.NUMBER`. | Dashboards page | **DONE for the page** (band names the active company's own currency); our OWL dashboards already format from the company currency (no hardcoded `$` in our code) | `test_export.TestDashboardContext` (AED company, EUR company, two companies, no-access) | AED company: "Amounts in AED" on Invoicing; two companies: mixed-currency warning | Odoo's own cards stay symbol-less; the band is the label |
| DASH-UX-02 | P0 | Sample vs real unclear. Also found: with two companies ticked Odoo **adds their figures without conversion** (the empty AED company showed the other company's USD 2,690). | Dashboards page | **DONE** (amber "Sample data" banner; mixed-currency warning naming each company and currency) | same | screenshots: sample Sales (banner), Invoicing with two companies (warning) | single-company selection remains the user's action |
| DASH-UX-03 | P1 | Metrics unexplained. **DSO as Odoo computes it = current-year receivable ledger balance ÷ (income − direct costs) × 365**: it divides by gross profit, not revenue, and nets customer credit, so it overstates days when direct costs exist. | Invoicing, Sales | **DONE for Invoicing and Sales** ("About these figures", Arabic label "متوسط فترة تحصيل المستحقات (DSO)") | definition test asserts the caveat | Invoicing shows the band and panel | other Odoo dashboards have no definitions (formulas not read); **no corrected DSO KPI was built** (needs a product decision on the formula) |
| DASH-UX-04..06 | P1 | Premium redesign of Sales / Invoicing, shared design system, chart labels | all | **NOT DONE** | — | — | Odoo's spreadsheet dashboards are Odoo-owned documents; a redesign means replacing their data files or building our own dashboard, which is an architecture decision |
| DASH-UX-07 | P1 | English / Arabic / mobile | Dashboards page | PARTIAL: Dashboards page EN+mobile (earlier), AED band EN | — | see above | invoice filters/sort, Document Layout, product image, chatter collapse/draft in English still not run |
| DASH-UX-08 | P1 | Export reconciliation | Dashboards page | previously verified; unchanged | `test_export` (12) | earlier | the band is not part of the exported file |
| REL-01 | P0 | Security / release | all | module `ncollection_dashboard_export` 19.0.1.0.0 → 19.0.1.1.0 | — | — | final regression not re-run on this revision |

## 5. Sales / Invoicing analysis — release additions and tracker update

Architecture (approved): NCollection-owned Sales and Invoicing dashboards; Odoo's Spreadsheet Dashboards, Share and our Export stay.
Data service `ncollection.premium.dashboard` + server-built Excel/PDF live in `ncollection_account_reports`; the OWL front end, menus and
Arabic in `ncollection_account_dashboard`; the shared scroll contract (`o_nc_prem`) in `ncollection_branding`.

| Module | Current (runtime) | Target | Migration |
|---|---|---|---|
| `ncollection_account_reports` | 19.0.1.12.0 | **19.0.1.14.0** | `-u`: two abstract models, one HTTP route, Arabic |
| `ncollection_account_dashboard` | 19.0.1.3.0 | **19.0.1.6.0** | `-u`: two client actions, three menus, shared design system (header chips, KPI definitions, polarity, currency-aware charts), assets, Arabic |
| `ncollection_branding` | 19.0.1.18.0 | **19.0.1.19.0** | `-u`: stylesheet only (scroll contract) |
| `ncollection_dashboard_export` | — | **19.0.1.1.0** | `-i` (context band, mixed-currency warning) |
| `ncollection_core` / `ncollection_account_core` / `ncollection_subscription` | unchanged by this addition | as §3 | as §3 |

Menus: *Accounting › Dashboards › Sales Analysis / Invoicing Analysis* (Accountant, CEO, Owner) and *Sales Analysis* under the department
dashboards (Sales role). The RPC mirrors them (`SALES_GROUPS`, `FINANCIAL_GATE_GROUPS`).

| ID | Status | Evidence | Remaining |
|---|---|---|---|
| DASH-UX-01/02 | DONE | AED company with data, empty AED company, EUR company, two companies ticked: `PD` tests + browser RPC; sample data banner kept | none |
| DASH-UX-03 | DONE | KPI tooltips (click or hover) with definition; DSO formula, Arabic title "متوسط فترة تحصيل المستحقات (DSO)", undefined ≠ zero | — |
| DASH-UX-04 | DONE for Sales and Invoicing | browser screenshots: English desktop (Invoicing, Sales), Arabic RTL desktop, Arabic 375 px, empty company | the pane was hidden, so every page load was slow |
| DASH-UX-05/06 | PARTIAL | shared component, tokens and responsive grid used by the two new dashboards; the older Finance / Accountant / Cash / CEO / department dashboards were **not** restyled | restyle is a follow-up |
| DASH-UX-07 | PARTIAL | see the report | English invoice filters / sorting, Document Layout, product image, chatter collapse / draft were **not** run this round |
| DASH-UX-08 | DONE | Spreadsheet Dashboards export = faithful snapshot (stated); Sales / Invoicing export = server-calculated | — |

## 6. Final local release checklist (not executed)

Tested revision for the full regression: `6528dc4` (3,360 tests, 0 failed); later commits `6bb457f` change only `self.env._` call style (module tests 73/73, pylint 205).
Browser acceptance: `e2e/dashboards/` (41/41). Rehearsed on disposable copies: fixture, platform (`saastest`), accounting tenant (`ncollection`), UAE tenant (`e2euae`): exit 0, second `-u` a no-op, translations loaded, existing documents backfill to 0.

1. Back up every database (release procedure backup volume) and keep the previous source tree: this is the rollback.
2. Deploy the code; each database answers the branded 503 until upgraded (release gate).
3. Per database: `-u <its modules> -i ncollection_dashboard_export` (never `-u all`), then `_update_translations(['ar_001'])` + `clear_all_caches`.
   Modules: core 1.28.0, account_core 1.13.0, account_reports 1.14.0, account_dashboard 1.6.0, branding 1.19.0, subscription 1.19.0, dashboard_export 1.1.0.
4. Nginx (separate approval): `nginx -t` then reload with the committed upstream re-resolution (section 2).
5. Smoke: `e2e/dashboards/survey.js` against the upgraded tenant (read-only pages) and the Finance / Sales / Invoicing pages by hand.
