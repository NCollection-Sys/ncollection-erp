# Reporting architecture audit — Odoo 19 vs OCA 19.0 vs NCollection

Status: **Phase 1 (audit) delivered; Phase 2 (migration) is a decision for the owner** — see §8.
Evidence: read from the pinned checkouts in `./oca` (`repos.yml`, 2026-07-19 pins), the running
databases (`atlas`, platform), and measurements taken on a scratch database that has since been
dropped. Nothing here was installed to produce it.

## 1. Three defects found and fixed (root causes)

| # | Symptom | Root cause | Fix / guard |
|---|---|---|---|
| 1 | Home, Reports hub, every report and the dashboards could not scroll; the bottom of the page was clipped | `.o_action_manager` is a fixed-height `overflow:hidden` box. Native views scroll because Odoo wraps them in `.o_action`/`.o_content`; our OWL client actions mount **directly** in the manager and declared `overflow-y:auto` + `min-height:100%` with no height, so the component grew to its content (`clientHeight == scrollHeight`), its own overflow never engaged, and the parent silently clipped it (measured 1407 px of content in 722 px) | `ncollection_branding/static/src/scss/app_shell.scss`: every client-action root gets `block-size:100%` + `overflow-y:auto`. `test_app_shell_scroll.py` walks the registered client actions and fails when a root is not listed (mutation-checked). Browser audit: 17 pages × 5 widths × EN/AR, no clipping, no header overlap, no page-level horizontal overflow |
| 2 | PDF: `ValueError: incomplete format` in `report_financial` | The template formatted a percentage with a Python `%` **inside a QWeb attribute** (`'%.2f%%' % x`). QWeb runs its own `%` pass over the compiled expression, collapsing `%%` to `%`. It broke **every** report with a percent column: Balance Sheet, P&L, Cash Flow, Financial Summary, Revenue/Expense/Profitability, Cost/Profit-Centre, Department (10 of 20 models; reproduced before the fix) | One formatting point, `_nc_export_payload()`, feeds both PDF and XLSX; the template only prints strings. A test forbids `%` formatting in any report template attribute. Every engine report is rendered to HTML and to a real PDF (HttpCase + wkhtmltopdf) |
| 3 | Partner Ledger / Statement search: `Cannot read properties of undefined (reading 'state')` | The picker handlers were `(ev) => this.searchRelation(field(fname), …)`. `field()` is a component method; in a render expression OWL calls it on the component, but inside an arrow function that runs later it is a detached call with no `this`. Not a lifecycle, URL-state or language problem (ruled out) | `this.field(fname)`; out-of-order answers are discarded; `test_owl_template_rules.py` rejects any bare call inside a `t-on-*` handler; `e2e/manual/reports/search.js` covers type / clear / pick / change date / change report / EN↔AR / reload / back-forward |

## 2. Current NCollection reporting architecture

```
menu ─► client action (report_viewer, context.nc_report_model)
          │ orm.call
          ▼
ncollection.report.service.nc_report_run ─► engine wizard (TransientModel, ir.rule per create_uid)
          │                                   _nc_columns() + _nc_compute_lines()  ◄── reads account.move.line only
          ▼                                   (the SAME rows feed the PDF and the XLSX)
JSON rows with ids ─► OWL viewer (sections, drill-down links, filters, URL state)
```

* 20 concrete report models on one base (`ncollection.account.report`): filters, opening balances,
  FY-aware logic, comparison periods. Calculations are `read_group`/ORM reads of Odoo's own data.
* Print channels now share `_nc_export_payload()` (formatting once, language separators, currency
  decimals, filters, generated timestamp, user, direction).
* **Measured performance** (scratch DB, then dropped): GL 200 lines 0.06 s · 2.2 k lines 0.32 s ·
  10 k lines 0.98 s (XLSX 3.2 s); Trial Balance/Balance Sheet/P&L ≤ 0.04 s at every size; Partner
  Ledger 10 k lines 0.25 s. Browser: 10 k-line GL opens collapsed in 2.4 s; Expand-all 0.6 s (sections
  page at 400 rows); Partner Ledger expand-all 5 700 rows in 2.7 s.

## 3. Odoo 19 Community — what is really there

* **Ships:** list/pivot/graph analysis views — `account.move.line`, `account.invoice.report`,
  `sale.report`, `purchase.report`, `stock.quant`/`stock.move.line`, `account.analytic.line`, the
  stock valuation report (`stock_account`).
* **Ships definitions, not a renderer:** 4 `account.report` records (UAE *VAT201 Form*, *Generic Tax
  report* and two group-bys). The renderer, Balance Sheet, P&L, Trial Balance, General Ledger, Aged
  Receivable/Payable, Partner Ledger, Cash Flow and PDF/XLSX export of them are **Enterprise
  (`account_reports`)** and are absent. Native Community therefore cannot be the source for the
  financial statements.

## 4. OCA 19.0 (pinned, official) — module review

| Module | Ver | Installable | Licence | Depends (beyond account) | In use? |
|---|---|---|---|---|---|
| `account_financial_report` | 19.0.0.0.15 | yes | AGPL-3 | `date_range`, `report_xlsx` | yes (platform + 4 tenants) |
| `partner_statement` | 19.0.1.1.0 | yes | AGPL-3 | `report_xlsx`, `report_xlsx_helper` | **no** (in the pinned repo, not installed) |
| `account_tax_balance` | 19.0.1.0.2 | yes | AGPL-3 | `date_range` | **no** |
| `mis_builder` (+`_budget`) | 19.0.1.0.1 | yes | AGPL-3 | `board`, `report_xlsx`, `date_range` | yes (BS and P&L templates) |
| `report_xlsx` / `report_csv` | 19.0.1.0.x | yes | AGPL-3 | `xlsxwriter` (+`xlrd`) | `report_xlsx` yes |
| `mis_builder_cash_flow` | — | **not in the pinned checkout** — cannot be assessed offline; needs a 19.0-branch check before any claim | | | no |

`account_financial_report` provides General Ledger, Trial Balance, Open Items, Aged Partner Balance,
Journal Ledger and VAT Report, each as HTML + PDF + XLSX, with filters for date range, company,
journals, accounts, partners, analytic accounts, foreign currency, hide-zero and centralisation, and 8
test files. `partner_statement` provides Activity, Detailed Activity and Outstanding statements (PDF +
XLSX, e-mail sending, ageing buckets, multi-currency). `account_tax_balance` lists tax balances by
tax/group per period with drill-down to move lines.

## 5. Capability matrix

Legend: **N** = NCollection engine (now) · **E** = Odoo Community native · **O** = OCA 19.0 pinned ·
PDF/XLSX/Drill/Filter/RTL describe the **recommended** source today. "Cx" = migration complexity.

### Financial
| Report | N | E | O | PDF | XLSX | Drill | Filters | Grouping | RTL | Recommended source | Cx |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Balance Sheet | yes (comparison, drill) | no | MIS template (`ncollection_mis_templates`) | N yes | N yes | account→items | date, cmp, journals, accounts | section tree | PDF+XLSX yes | **Keep N** (MIS stays an option for custom layouts) | — |
| Profit & Loss | yes | no | MIS template | yes | yes | yes | same | tree | yes | **Keep N** | — |
| Trial Balance | yes | no | `account_financial_report` | yes | yes | account→items | date, journals, accounts, partners | flat | yes | **N now; O is the richer engine** (hierarchy, foreign currency, hide-zero, analytic) → decision §8 | M |
| General Ledger | yes (10 k rows ≈ 1 s) | no | `account_financial_report` | yes | yes | account/entry/item/partner/payment/orders | date, journals, accounts, partners | per account | yes | **N now; O richer** (centralisation, foreign currency) → §8 | M |
| Cash Flow | yes | no | `mis_builder_cash_flow` not at this pin | yes | yes | account | date, cmp | sections | yes | **Keep N** | — |
| Financial Summary | yes | no | none | yes | yes | yes | date, cmp | tree | yes | **Keep N** (NCollection-specific) | — |
| Statement of Changes in Equity | yes | no | none | yes | yes | yes | date | tree | yes | **Keep N** | — |

### Receivables / Payables
| Report | N | E | O | PDF | XLSX | Drill | Recommended | Cx |
|---|---|---|---|---|---|---|---|---|
| Partner Ledger | yes | no (journal items grouped by partner only) | — (OCA GL can group by partner) | yes | yes | partner/entry/item/payment | **Keep N** | — |
| Customer / Vendor Statement | yes (one doc per partner) | no | `partner_statement` (3 statement types, e-mail, ageing buckets, multi-currency) | yes | yes | partner | **Adopt O alongside, additive** (statements are a partner-facing document; OCA is more complete) → §8 | S |
| Aged Receivable / Payable | yes (+ invoice drill) | no | `account_financial_report` | yes | yes | partner→invoice→payment | **N now; O richer** (configurable buckets) → §8 | M |
| Open Items | no | no | `account_financial_report` | — | — | — | **Keep O** (already filed in the hub) | — |

### Accounting / Tax
| Report | N | E | O | Recommended |
|---|---|---|---|---|
| Journal Entries / Items | list via drill | yes (list/pivot) | — | **Native** |
| Journal Ledger | no | no | yes | **Keep O** (in hub) |
| Account Activity / Analysis | GL + Trial Balance | `account.move.line` pivot | OCA GL | **Native pivot + N** |
| Tax Report | UAE VAT 201 (FTA form) | definitions only, no renderer | `account_financial_report` VAT Report | **Keep both**: N for the FTA 201 return, O VAT Report for tax-code detail |
| Tax Balance / by customer / by vendor | no | `account.move.line` pivot grouped by tax/partner | `account_tax_balance` | **Gap — adopt `account_tax_balance`** (additive, §8) |

### Sales · Purchases · Inventory · Management
| Report | N | E | O | Recommended |
|---|---|---|---|---|
| Sales Analysis / by Customer / Product / Salesperson | — | `sale.report` pivot+graph (4 menus) | — | **Native** (done) |
| Sales by Category | — | `sale.report` group-by category | — | **Native** (group-by; no new code) |
| Customer Profitability | — | needs `sale_margin` (not installed) | — | **Gap** — install `sale_margin` (Odoo core module, no OCA) or derive in Profitability |
| Purchase Analysis / by Vendor / by Product | — | `purchase.report` (3 menus) | — | **Native** (done) |
| Vendor Spend Analysis | — | `purchase.report` pivot by vendor over time | — | **Native** |
| Inventory Valuation | — | stock valuation report | — | **Native** |
| Stock Movement / Product Stock / Warehouse | — | `stock.move.line`, `stock.quant`, `stock.move` | — | **Native** (done) |
| Inventory Aging | — | **not in Community** | — | **Gap** — needs a small NCollection report over `stock.quant.in_date` (no OCA equivalent was found in the pinned repos) |
| Budget vs Actual | yes | no | `mis_builder_budget` | **Keep N** (approval workflow already built) |
| Revenue / Expense / Gross Profit | yes (Revenue, Expense, Profitability) | no | — | **Keep N** |
| KPI Dashboard | yes (4 finance dashboards + CEO) | no | `mis_builder` dashboard | **Keep N** |

## 6. Recommended target architecture

```
Odoo data (account.move.line, sale/purchase/stock reports)
   ├─ statements & NCollection-specific reports ........ NCollection engine (kept)
   ├─ GL / TB / Aged / Open Items / Journal Ledger ..... OCA account_financial_report (decision §8)
   ├─ partner statements ............................... OCA partner_statement (additive)
   ├─ tax balances ..................................... OCA account_tax_balance (additive)
   └─ sales / purchase / stock analysis ................ Odoo native pivots (filed + translated)
                         ▼
        NCollection hub + viewer + PDF/XLSX shell + security + drill-down
```

## 7. Migration plan (only if §8 is approved)

1. **Adapter, not rewrite.** `ncollection.report.service` gains a *source* per catalogue entry
   (`engine` | `oca`). For `oca` sources the viewer calls the OCA wizard's `_get_report_values()` and maps
   the result to the existing row/column/id contract, so the hub, language state, drill-down links and
   exports are unchanged.
2. **Pilot on Trial Balance** (smallest OCA surface); compare totals to the native engine on the
   platform dataset and the tenants (a reconciliation test, both directions).
3. **General Ledger, Aged, Open Items** next, one per release; the NCollection model stays installed and
   hidden until its OCA counterpart has been in use for one release.
4. **Additive modules** (`partner_statement`, `account_tax_balance`) install behind plan licensing.
5. **Sunset notes** (`OCA_DEPENDENCIES.md`, ADR #15/#117) rewritten from "retire OCA" to "OCA is the
   calculation source where richer; NCollection owns UX, security, branding".

## 8. Decisions required (why this stops here)

* **Reversing ADR #15 / #117.** The authoritative architecture documents say the OCA financial reports
  are a *temporary bootstrap to be retired* in favour of native `ncollection_*` reports. Your brief
  prefers OCA where stronger. Those two instructions conflict, and `CLAUDE.md` requires stopping before
  changing a documented architectural decision or adding an OCA module (`partner_statement`,
  `account_tax_balance`). **I have not installed or switched anything.** Approve (a) the direction
  (Trial Balance / GL / Aged → OCA behind the NCollection viewer) and (b) the two additive modules, and
  the plan in §7 can proceed incrementally.
* **Not verifiable offline:** `mis_builder_cash_flow` and any OCA module outside the pinned repos —
  availability on the 19.0 branch must be checked online before relying on them.

## 9. Risks

* AGPL-3 modules are already consumed; adding two more does not change the licence posture, but the
  `ncollection_*` LGPL modules must keep depending on OCA only through the adapter boundary.
* The OCA wizards persist per-run data in their own transient models; the adapter must keep the
  per-user `ir.rule` isolation the NCollection engine has (tested by `test_report_security.py`).
* Two engines producing the same figure is a support risk during the pilot; mitigated by the
  reconciliation test and by hiding (not deleting) the superseded report for one release.
* Translations: OCA ships its own Arabic; consistency with the NCollection glossary needs a review pass.

## 10. Scope estimate

| Work | Size |
|---|---|
| §1 defects, PDF/XLSX standard, viewer search | **done** |
| Adapter + Trial Balance pilot + reconciliation test | ~3–4 days |
| GL, Aged, Open Items through the adapter | ~4–5 days |
| `partner_statement` + `account_tax_balance` (licence gating, hub entries, Arabic) | ~2 days |
| Inventory Aging + Customer Profitability gaps | ~2–3 days |
| Documentation + sunset-note rewrite | ~1 day |
