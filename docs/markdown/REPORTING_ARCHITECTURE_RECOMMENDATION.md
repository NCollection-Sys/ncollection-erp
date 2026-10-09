# Reporting architecture recommendation

Date: 2026-10-09 · Evidence: [ACCOUNTING_REPORTING_CAPABILITY_MATRIX.md](ACCOUNTING_REPORTING_CAPABILITY_MATRIX.md)
(100 audited items) · Builds on [REPORTING_ARCHITECTURE_AUDIT.md](REPORTING_ARCHITECTURE_AUDIT.md) and
[REPORTING_TRIAL_BALANCE_PILOT.md](REPORTING_TRIAL_BALANCE_PILOT.md). Nothing was pushed or merged.

## 1. Verdict

**Not complete.** The accounting core (ledgers, statements, ageing, partner statements, tax analysis, VAT 201,
budgets, assets, dashboards) is complete, verified in a real browser, and its PDFs and workbooks hold up on
14,796 posted lines in English and Arabic. What was missing was not another statement:

| # | Finding | Impact | Status |
|---|---|---|---|
| 1 | Sales / Purchases / Inventory reports were gated on Odoo's **Administrator** groups, which no NCollection role has | Those three hub categories were **empty for every role, Owner and CEO included**. Only a superuser saw them, so every admin-run test passed | **Fixed** for Sales + Inventory; **Purchases needs a role decision (D-1)** |
| 2 | OCA ledger wizards were granted to *any internal user* | A salesperson or department manager could pull the whole general ledger / trial balance over RPC (menu hidden, ORM open) | **Fixed + guarded** |
| 3 | The report service accepted another user's run id | Returned an export action for a run that is not yours (the download routes themselves refused, 403) | **Fixed + tested** |
| 4 | PDF "Arbitrary Uncaught Python Exception" | See §3 — **not an Odoo error** | **Root-caused; client made resilient** |
| 5 | PDFs unstyled in dev | The odoo container could not resolve `<tenant>.localhost`; wkhtmltopdf lost the letterhead and stylesheet and paginated differently (377 vs 232 pages for the same ledger) | **Fixed for dev** (`make pdf-hosts`, `make doctor`) |
| 6 | No tax analysis beyond the FTA 201 boxes | No output/input tax by tax, account, customer, vendor, or taxability | **Built** (§6) |
| 7 | 7 distinct reports genuinely missing (8 rows), 13 partial rows | Stock aging / slow-moving, sales vs target, profitability by customer/product, payment history | **Planned** (§7) |

## 2. How complete is Odoo Community?

For *analysis* — substantial. `sale.report`, `purchase.report`, `account.invoice.report`, stock moves/quants,
`vendor.delay.report` and CRM reports give pivot/graph/list with native filters, group-bys, company rules and
XLSX export, in Arabic and RTL. Sales has discount, team, category, warehouse, salesperson and quotation/order
state; purchases has lead-time (`delay`, `delay_pass`), price, billed/received; invoices carry credit notes,
due dates, payment state and (with product cost) `price_margin`.

For *financial statements and accounting reports* — **absent**. Four `account.report` tax definitions exist; the
renderer, Balance Sheet, P&L, Trial Balance, General Ledger, Aged, Partner Ledger, Cash Flow, assets and PDF
export are Enterprise. It also has **no PDF** for any pivot/graph, **no stock aging / slow-moving / turnover**,
**no sales-vs-target report**, and no follow-up/dunning.

## 3. PDF — exact root cause

**Symptom** (user): `RPC_ERROR — Arbitrary Uncaught Python Exception`, on `ncollection.localhost`.

**Reproduced** from the real toolbar (Print → PDF) in Chrome and in Edge on the development machine. Captured at
the browser's network layer (Chrome DevTools Protocol), for `POST /report/download`:

```
statusCode 204
headersText "HTTP/1.0 204 Intercepted by the IDM Advanced Integration
             Cache-Control: no-cache / Pragma: no-cache / Connection: close"
```

**Exact cause:** *Internet Download Manager* (a Windows download manager that hooks the network stack, not just
the browser) swallows the PDF download response and replaces it with an empty `204`. Odoo's client helper
(`web/static/src/core/network/download.js`, `configureBlobDownloadXHR`) treats any non-200 as a server error: it
fails to parse a JSON error from the empty body and falls back to the literal message **"Arbitrary Uncaught
Python Exception"** — which is where the text in the dialog comes from. There is **no Python exception**: Odoo's
own log for the same click reads `The PDF report has been generated … "POST /report/download HTTP/1.1" 200`.

**Ruled out (so nobody re-investigates):** the previous QWeb `%` fix (correct, and necessary, but not this);
the report template; wkhtmltopdf; the controller; CSRF; multipart vs urlencoded; Origin / Sec-Fetch headers;
service worker; IPv6 vs IPv4; Arabic filename; the nginx edge (port 8069 is Odoo directly). The identical
request sent from outside the browser returns `200 application/pdf` with `Content-Disposition: attachment`.
A stock Odoo report (`base.report_irmodulereference`) fails the same way, which is the control.

**What was fixed**

* The viewer now catches a failed PDF download, says *"The PDF download was blocked by the browser or a download
  manager. Opening the report in a new tab instead."* (Arabic included) and opens the PDF route — instead of a
  stack-trace dialog. Verified in the real browser: no error dialog, a warning, a new tab.
* **The user must still exclude `localhost` in IDM** (Options → Site Exceptions / disable the Chrome integration for
  that host) for the download to save directly. This cannot be fixed from Odoo and I did not touch their IDM.
* **A second, genuine defect hid behind it (dev only).** wkhtmltopdf runs inside the odoo container and fetches
  the stylesheet, letterhead and logo from `web.base.url`. The container could not resolve `<tenant>.localhost`
  (`wkhtmltopdf: Exit with code 1 due to network error: HostNotFoundError` in the Odoo log), so Odoo returned an
  **unstyled PDF with exit 0**. `scripts/dev/pdf_hosts.sh` (`make pdf-hosts`) adds the tenant names to the
  container's `/etc/hosts` — idempotent (17 added, then 0), re-run after a container restart — and
  `make doctor` now warns. Production is unaffected (its base URL is a real hostname).

**PDF validated on a normal install** (seeded tenant, 14,796 posted lines, 1,500 invoices, 900 bills, 40 credit
notes): 12 reports × English and Arabic, each fetched, opened and inspected (pages, page size, extracted text,
page numbers) and viewed in Chrome's PDF viewer: General Ledger (232 pp, landscape), Trial Balance, Balance
Sheet, P&L, Cash Flow, Partner Ledger (49 pp), Customer statement (133 pp, one document per partner), Vendor
statement (85 pp), Aged Receivable (6 pp), Aged Payable (4 pp), Tax Analysis ×2. Letterhead and company on every
page, repeated column header, "Page n / N" footer, A4 portrait/landscape as intended, Arabic fully right-to-left
with mirrored columns. XLSX for ten of them: real numeric cells (0 stringified numbers), frozen header, widths,
landscape fit-to-width, right-to-left sheet in Arabic.

**What is not proven:** the final *browser save* while IDM is active, on this machine — IDM hooks every browser
here, so the click cannot complete. Everything up to and including the bytes IDM intercepts is verified.

**Known PDF limits:** the report title appears on page 1 only (letterhead and column header repeat on every
page); very large ledgers are slow (232 pages ≈ 42 s, wkhtmltopdf-bound — budget ≥ 60 s or queue the export);
the first column's text sits against the cell border by about a pixel (cosmetic).

## 4. Scroll and partner search — status

* **Global scroll** (`.o_action_manager` fixed-height box clipping client-action roots): root cause and fix are
  unchanged and **re-verified** — `reach.js` (every page, EN + AR) and `qa1.js` (1920 / 1366 / 1024 / 768 / 480,
  EN + AR) pass; the new Tax Analysis page was checked at all five widths with no page-level horizontal overflow.
* **Partner / customer / vendor search** (`this` lost in an OWL arrow handler): **re-verified** by `search.js`
  (type, clear, pick, change date, change report, EN↔AR, reload, back/forward) and `lang_rep.js`; no console errors.

## 5. Target architecture

```
                         ┌────────── source engines ──────────┐
Odoo native analysis ──┐ │  Odoo native   OCA (pinned)  NCollection engine │
(sale / purchase /     ├─┤                                                  │
 stock / invoice)      │ └──────────────┬───────────────────────────────────┘
OCA ledgers ───────────┤                │  adapter implements the report contract
NCollection engines ───┘                ▼
                              NCollection report contract (_nc_columns / _nc_compute_lines)
                                         ▼
          Hub (7 categories) → Viewer (filters, URL state, RTL/LTR, drill-down links)
                                         ▼
                        PDF (one payload)  ·  XLSX (same payload)  ·  server-side ACL + own-run rule
```

Rules: **the user never learns the source.** Native analysis opens as native pivot/graph (they already look
right); anything with a printable statement runs on the engine; OCA is reached only through the adapter.
Source is chosen per report on evidence (the Trial Balance pilot is the template: correct-but-not-better ⇒ keep
native). Security is enforced at the ORM, never by menu hiding.

| Class | Reports |
|---|---|
| **Odoo native (keep)** | Sales / Purchase / Invoice / CRM analysis, stock moves and quants, vendor delay, analytic lines, payments |
| **NCollection engine (keep)** | GL, TB, BS, P&L, CF, Equity, Financial Summary, Partner Ledger, Statements, Aged, VAT 201, **Tax Analysis**, Budget vs Actual, Asset Register, centre/department analysis, Revenue/Expense/Profitability, dashboards |
| **Adapter-backed (build, then hide the raw OCA screen)** | Open Items, Journal Ledger, VAT Report — currently the *raw OCA wizard* opened from a hub card; same UX/ACL/Arabic gap |
| **Deprecate in the hub, keep the model** | Account Balances (duplicate of Trial Balance; the engine's regression fixture); hidden-by-group "Inventory Valuation" card |
| **Do not install** | `account_tax_balance` (duplicates Tax Analysis), `report_csv`, `bi_sql_editor` (tenant-authored SQL is a surface nobody asked for), `kpi` |
| **Kept only for compatibility** | OCA TB adapter (off by default), `mis_builder` templates for custom layouts |

Hub taxonomy (unchanged, now complete in content): Financial & Accounting · Receivables & Payables · Sales ·
Purchases · Inventory · Tax · Management & Analytics.

## 6. What changed in code (all additive, tested)

* `ncollection_account_reports`: **Tax Analysis** engine (tax / tax account / customer / vendor / taxable-vs-not),
  reading Odoo's own tax and base lines, signed so credit notes net out, 0 % taxes show their base. 16 tests;
  mutation-checked (flipping the sign rule fails 7).
* Curated menu-group map so native Sales / Purchase / Inventory analysis opens for the app's ordinary user (the
  least group that already holds the ORM right) — grants no data access (UI was stricter than the ORM).
* `_nc_harden_oca_wizards`: the six OCA report wizards restricted to Accounting read-only on every registry load
  (an OCA upgrade resets it). Tests *attempt* the create as a plain internal user; mutation-checked (fails 7).
* Service: `nc_report_export` / `nc_report_detail` now refuse another user's run id.
* Viewer: resilient PDF download; links for tax and partner rows. Arabic for every new string.
* Dev tooling: `make pdf-hosts`, doctor check, and QA scripts (`tax_analysis`, `pdf_quality`, `xlsx_quality`).

## 7. Module adoption list

| Module | Source | Reason | Dependency | Risk | Adapter? | Decision |
|---|---|---|---|---|---|---|
| `account_financial_report` | OCA, pinned, installed | Open Items, Journal Ledger, VAT Report | date_range, report_xlsx | AGPL; wizards were open to all users (now hardened); partial Arabic | **Yes** — 3 adapters | **Adopt via adapter** (M each) |
| `mis_builder` (+budget) | OCA, pinned, installed | Custom management layouts | board, date_range | AGPL; no Arabic | No | Keep, secondary |
| `partner_statement` | OCA, pinned, not installed | E-mailed statements, 3 types | report_xlsx_helper | **No Arabic**; duplicates NCollection statements | Yes if adopted | **Defer** (needs Arabic + a requirement) |
| `account_tax_balance` | OCA, pinned, not installed | Tax balances | date_range | **No Arabic**; duplicates Tax Analysis | Yes | **Do not install** |
| `report_xlsx` / `_helper` | OCA, pinned, installed | XLSX infrastructure for OCA reports | xlsxwriter | AGPL | — | Keep |
| `bi_sql_editor`, `kpi`, `report_csv`, `report_py3o` | OCA, pinned, not installed | — | various | SQL authoring by tenants; duplicates KPI engine | — | **Do not install** |
| `sales_team_invoiced_target_report` | OCA sale-reporting 19.0, **unpinned** | Sales vs Target | sale, crm | New repo pin; AGPL; unverified here | Not needed (SQL report) | **Approval needed** |
| `partner_time_to_pay` | OCA account-invoice-reporting 19.0, **unpinned**, Beta | Days-to-pay | account | Beta | No | Optional; native payment list first |
| `stock_move_value_report` | OCA stock-logistics-reporting 19.0, **unpinned** | Value per stock move | stock_account | New repo pin | No | Optional |
| `mis_builder_cash_flow`, `account_ebitda_report` | OCA, unpinned, Beta/new | — | mis_builder | not needed | — | No |
| `sale_margin` | **Odoo core**, not installed | Margin on sales analysis | sale | Changes module state | No | **Approval needed** |
| `ncollection_inventory_reports` | **New NCollection module** | Stock aging, slow-moving, turnover | stock, stock_account | A 24th module; finance modules must not depend on `stock` | n/a | **Approval needed** |

## 8. Decisions required (not taken — each changes a documented security or architecture decision)

* **D-1 Purchasing visibility.** No role holds a Purchase group, so Owner/CEO/Manager cannot open Purchase
  Analysis. Proposal that respects the role matrix (CEO read-only, Manager no financial write): add **read-only
  ACL on `purchase.report` and `vendor.delay.report` for the CEO and Manager roles** (SQL views — no write
  surface), and let Owner imply Purchase Manager. Needs `ROLE_MATRIX.md` §2 + its checker updated.
* **D-2 Journal items readable by Sales / Manager.** Odoo core grants `account.move.line` read to those roles,
  which contradicts "Manager gets no financial visibility" (D5). The reports are protected; the raw model is not.
  Proposal: a global record rule limiting journal items to Accounting groups plus the user's own documents.
* **D-3 Stock moves/quants readable by every internal user** (Odoo default). Proposal: tighten only if a role
  that needs them is confirmed not to — Sales needs availability.
* **D-4** Pin `OCA/sale-reporting` and/or install `sale_margin` (Sales vs Target; margin).
* **D-5** Approve `ncollection_inventory_reports`.
* **D-6** Consolidated multi-company reporting — an architecture question (DB-per-tenant vs multi-company).
* **D-7** UAE VAT 201 classifies boxes by tax **name**; move to Odoo's l10n_ae tax tags before relying on it for
  a tenant whose taxes are named differently.

## 9. Test plan

| Layer | What | Where |
|---|---|---|
| Unit / ORM | Tax Analysis figures vs the invoices' own `amount_tax`; signs; 0 % base; every view prints; own-run privacy; foreign run id refused | `tests/test_tax_analysis.py`, `test_report_security.py` |
| Access | Plain internal user cannot create any OCA wizard; accountant can; idempotent; survives an OCA upgrade | `tests/test_oca_wizard_access.py` |
| Menus | Curated groups replace the Administrator gate; a missing module never widens a menu | `tests/test_report_hub.py` |
| Server-side per role | 8 roles × 2 tenants × (native models, engines, OCA wizards, cross-user PDF/XLSX routes) | probe scripts, results in the matrix |
| Browser | Hub per role; Tax Analysis journey; EN↔AR no F5; scroll at 5 widths; search; drill-down | `e2e/manual/reports/*` |
| PDF / XLSX | Real files inspected: pages, size, text, RTL, numeric cells, frozen header | `pdf_quality.js`, `xlsx_quality.js` |
| Regression | Full CI matrix, flake8, invariants, architecture guard, role matrix, doc counts, skip gate | CI + pre-push |

## 10. Roadmap

**P0 (done here):** discoverability of Sales/Inventory reports, OCA wizard ACL, service hardening, Tax Analysis,
PDF resilience + dev DNS, QA tooling. **P0 (needs you):** D-1.
**P1:** hub entries over native data (payment history, vendor bill analysis, customer/product profitability,
vendor on-time delivery, scrap, replenishment) — ~6 × S; Open Items / Journal Ledger / VAT adapters — 3 × M;
Trial Balance comparison — S; Working-capital KPI — S.
**P2 (approval):** Stock Aging / Slow-moving / Turnover (M+S+M), Sales vs Target, VAT 201 on tax tags (L).
