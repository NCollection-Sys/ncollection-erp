# Post-upgrade enhancements: requirement matrix, status, release matrix (branch `feature/post-upgrade-enhancements`)

Base: `236a7ff` (the release installed in `ncollection`, `awtar`, `ncollection_demo`). Nothing here has been applied to a shared database or the
shared runtime. Every result below was measured on disposable copies (private Postgres, internal network); "browser" means a real Chromium
(Edge) session against such a copy.

Legend: **DONE** implemented and tested; **PARTIAL** implemented, with the missing part named; **NOT DONE** not built; **PROVIDER** needs a live
provider / sandbox that was not used (not permitted).

## 1. Invoice customisation (`ncollection_account_invoice_layout`)

| Item | Status | Evidence / gap |
|---|---|---|
| 1.1 show / hide every line column, optional-columns selector | DONE | Discount, Amount, Total incl. Tax are optional columns of the standard selector (Odoo remembers the choice per user); customer invoices and vendor bills share the view |
| 1.1 reorder columns, saved order, company default | **NOT DONE** | needs an OWL list-renderer extension; documented in INVOICE_LAYOUT_AND_CALCULATIONS.md section 6 |
| 1.1 centred headers incl. Label, EN / AR / RTL, narrow screens | DONE | SCSS; browser check: every header `text-align: center` (EN and AR) |
| 1.2 Discount column, two-way with Disc.%, fixed vs percentage mode | DONE | 18 tests + browser; exact percentage fed to the engine, books to the cent |
| 1.3 editable Amount (solves the unit price) | DONE | tests + real list editing in the browser (360.00 typed -> price 200, Disc.% 10 kept) |
| 1.4 editable Total incl. Tax | DONE | tests (percentage, grouped, price-included handled by the engine's affine probe); refusals for zero quantity / 100 % discount |
| 1.5 charges and discounts below the lines (freight, global discount, configurable) | DONE | data-defined types, real invoice lines, calculation order, tests for customer invoice, vendor bill, credit note, posted refusal |
| 1.6 central configuration area, ACL, audit of changes | PARTIAL | Accounting > Configuration > Invoice Layout & Calculations > Charges & Discounts (Accounting Manager only, ORM-enforced, company rule). Column order configuration is part of the NOT DONE above. Configuration changes are not written to the audit log |
| 1.7 Total Discount / Total After Discount above Untaxed Amount | DONE | EN / AR browser; hidden when nothing is discounted |
| 1.8 tests | PARTIAL | 33 module tests + browser (EN, AR); responsive layout of the new columns checked at 1440 px only |

## 2. Anomaly Alerts removal: DONE

`ncollection.alert`, its four detectors, the two crons, the menu / action / views, the four record rules, the nine access rows and the Home tile are gone;
migration `19.0.1.29.0` archives any rows into `ncollection_alert_archive` (no model, nothing reads it) and drops the table (measured on copies of `ncollection`
and `ncollection_demo`: 0 rows, table, crons, menu and ir.model row all gone). Kept on purpose: the scheduler service user / group and its read-only ACLs (a
general non-interactive identity used by the administration service; its ACL sync moved to the core group hook), release-health monitoring, audit
modules, security alerts. No tenant provisioning default referenced the feature (search of the whole tree).

## 3. AI assistant

| Item | Status | Evidence / gap |
|---|---|---|
| 3.1 Odoo 20 AI research | **NOT DONE** | no authoritative source was consulted in this session; no claim is made |
| 3.2 useful, read-only, user-privileged answers | DONE (mock) | `ai_topics.py`: payables, overdue, sales, purchases, payments, stock; EN + AR keywords; run through the aggregation engine as the asking user; PII-pseudonymised; mock answers state real figures and say they are a demo; 166 module tests, 55 gateway tests |
| 3.2 write actions | by design none | nothing can create / post / cancel / delete |
| 3.3 provider adapters, admin-only key, retries | PARTIAL | the gateway already has the Anthropic adapter and the mock; no second provider, no tool-calling protocol; **PROVIDER**: no live answer was ever requested |
| 3.4 / 3.5 animation (bubble, panel, messages, busy ring) | DONE | CSS; browser: enter animation, icon rotation, exit animation, Escape, focus return, reduced motion, RTL origin, mobile sizing rule |
| 3.6 mobile / RTL screenshots | PARTIAL | desktop EN and AR checked; the mobile sizing rule is CSS only, not screenshot-verified |

## 4. UAE e-invoicing (local review)

Audit: `ncollection_uae_einvoicing` (engine, PINT-AE, isolated validator, append-only history, retention), `ncollection_uae_einvoicing_acube` (adapter, NOT proven on a
sandbox), `ncollection_account_localization_uae` (TRN / FTA). Status: DONE for visibility: installed on a copy of `ncollection_demo`; Accounting > UAE E-Invoicing
(E-Invoices, Received, Self-Billing, Configuration: Settings, Tax Mapping, Payment Means, Service Providers, Holidays) is shown to Accountants, Billing Managers
(`account.group_account_user` implies the user group), the Owner (`ROLE_IMPLICATIONS`, documented in ROLE_MATRIX.md) and system administrators. No A-Cube call was
made; `e2euae` and its three protected invoices were not touched. **PROVIDER**: A-Cube sandbox acceptance stays blocked.

## 5. Dashboard PDF exports: DONE

One page design (`ncollection.dashboard.pdf` in `ncollection_branding`): a dedicated A4 paper format (the cause was the company's letterhead format with its tall top margin
and thin sides), 14 mm sides, header (company, title, context), footer (company, title, page n / N), repeated table headers, no split rows, `dir=auto` cells, currency
codes spaced ("4,901AED" -> "4,901 AED"; display only, Excel unchanged), Arabic RTL. Sales, Invoicing, Summary and Spreadsheet Dashboards share it. Rendered and inspected
as PNG pages (EN and AR, 1 and 2 pages): margins, wrapping of very long labels, pagination. Findings fixed on the way: Arabic cells clipped by bidi (now `dir=auto`),
narrow first column, footer overflow.

## 6. Release matrix for the next shared upgrade (NOT executed)

| Database | `-u` | `-i` |
|---|---|---|
| `ncollection`, `awtar`, `ncollection_demo` | `ncollection_core`, `ncollection_branding`, `ncollection_account_reports`, `ncollection_dashboard_export`, `ncollection_ai`, `ncollection_account_audit` (Arabic catalogue, access) | `ncollection_account_invoice_layout` |
| `ncollection_demo` only (e-invoicing review) | as above | `ncollection_account_localization_uae`, `ncollection_uae_einvoicing`, `ncollection_uae_einvoicing_acube`; start `einvoice-validator` and `einvoice-validator-sb` (compose `docker-compose.einvoice.yml`, token from `.env`) |

Order: pass 1 `-u`, pass 2 `-i`, Arabic terms (`_update_translations`), drift check, as in RELEASE_LOCAL_UPGRADE_PLAN.md. Rollback: restore the pre-upgrade dump of each database;
`ncollection_alert_archive` goes with the database. `e2euae` and every other database stay out of every command.
