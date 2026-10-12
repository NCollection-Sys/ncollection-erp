# Applications, barcode, accounting defaults, zero balances — decision record

Audited 2026-10-12 against the `odoo:19` image and GitHub. Nothing here changes the architecture documents.

## 1. Applications: entitled / installed / visible

Three separate facts, never one flag.

| Fact | Owner | Where | Tenant can change? |
|---|---|---|---|
| **Entitled** (in the plan) | platform | `plan.allowed_module_names` (+ per-tenant overrides), synced to `ncollection.workspace.config` | no |
| **Installed** | platform | an isolated `odoo -i` run when the plan changes (`module_install.py`); the #459 lockout stays | no |
| **Visible on Home** (and order) | workspace Owner | `ncollection.launcher.app` (`Settings > Applications`) | yes — this only |

States a tenant can be in: installed + entitled + visible; installed + entitled + **hidden**; entitled, not yet installed
(shown as "in your plan, being installed"); not entitled ("not in your plan").

* Hiding changes **only the Home tile**. Menus, access rights, licensing (Ring 1/2) and data are untouched; showing grants nothing.
* `ncollection.launcher.app` is a status board + preference store filled by `_nc_sync()` from `ir.module.module` and the
  workspace config. `create`/`unlink` are superuser-only and `write` accepts exactly `visible` and `sequence`, enforced at the ORM.
* The old lockout message now points the Owner to Settings > Applications.
* Dependency apps: licensing `point_of_sale` pulls `stock`/`account` through the dependency closure, so their tiles appear too.
  The Owner can hide them; the default is unchanged (visible) so no existing plan changes behaviour.

### The seven requested apps (all verified in the Odoo 19 Community image, LGPL-3, none Enterprise)

| App | Technical name | Direct dependencies | Root-menu token (Ring 1) | OCA needed? |
|---|---|---|---|---|
| Point of Sale | `point_of_sale` | stock_account, barcodes, html_editor, digest, phone_validation, partner_autocomplete, iot_base, … | `point_of_sale` | no |
| Manufacturing | `mrp` | product, stock, resource | `mrp` | no |
| Recruitment | `hr_recruitment` | hr, calendar, utm, attachment_indexation, web_tour, digest | `hr_recruitment` | no |
| Fleet | `fleet` | base, mail | `fleet` | no |
| Live Chat | `im_livechat` | mail, rating, digest, utm | `im_livechat` | no |
| Attendances | `hr_attendance` | hr, barcodes, base_geolocalize | `hr_attendance` | no |
| SMS Marketing | `mass_mailing_sms` | portal, mass_mailing, sms | `mass_mailing_sms` | no |

All are `application=True`, so each already appears in the plan module picker; a platform test pins that. They are full Odoo
applications (POS registers/sessions/payments/returns, MRP BoM + manufacturing orders, recruitment pipeline, fleet vehicles/
contracts/costs, attendance check-in/out + kiosk, livechat channels/operators, SMS campaigns), not menu shells.
**SMS sending is not free**: the application is available, but delivery uses Odoo IAP credits or a configured provider
(`sms` / `sms_twilio`). `website` is **not** required for Live Chat (`/im_livechat/support/<id>` is served by `im_livechat`).
Dependency footprint (modules pulled in) — POS 28, SMS 23, Recruitment 19, MRP 18, Attendances 18, Live Chat 15, Fleet 8.

How a tenant gets one: add the technical name to the plan (or a per-tenant `enable` override); the plan write queues the install.
No code change is needed to *offer* an app; nothing is installed on a tenant until its plan says so.

## 2. Barcode

`stock_barcode` (the scanning app) is **Enterprise-only** — confirmed absent. The OCA port `stock_barcodes` is **not installable on 19.0**:
it exists on 16.0, and the 19.0 migration (OCA/stock-logistics-barcode#742) is an open, stale, unapproved PR that drops packaging
barcodes and needs a second new repo. Grafting an unmerged PR is the mistake recorded for `auth_brute_force` / `helpdesk_mgmt_sla`.
`OCA/wms` shopfloor has no 19.0 addons. Community does ship the data (product/location/lot barcodes, GS1 nomenclature) and the
scanner service + camera scanner, but no scan **workflow**.

**Decision: BUILD, thin** (DELIVERABLE_1 §3.2 and P7-T04/T07 already name "Barcode endpoints via custom addon"). No OCA pin, no `repos.yml` change.
`ncollection_stock_barcode` drives the stock engine and never replaces it: a scan sets `stock.move.line.quantity/picked/lot`,
validation is `button_validate` (backorder wizard included), a count writes `inventory_quantity` and applies with
`action_apply_inventory`. Everything runs as the caller (no sudo): a user scans only operations they could open by hand, in their company.
Revisit adopting `stock_barcodes` for a handheld backend UI only after OCA PR #725 and the 19.0 port are merged and tagged.

## 3. Accounting Defaults

`Settings > Accounting Defaults` (`ncollection_account_core`) — native `res.company` fields on one page for the Owner:
default receivable / payable, income / expense (Odoo also uses these as every product category's starting accounts), default
taxes, transfer + bank-suspense accounts, cash-difference accounts, FX journal + gain/loss, early-payment-discount accounts, and
the accrual accounts + journal behind "Change Period". Receivable/payable have no Odoo settings field, so they are computed fields
whose inverse writes the same `ir.default` the chart loader wrote (company-specific). A property default is a **live fallback**:
it applies to every customer/vendor with no account of its own; one with an explicit account keeps it.

**Deferred revenue / expense: not available in Community.** Odoo 19 CE has no `deferred_*_account_id` fields or generation engine
(Enterprise `account_accountant`); OCA has no `account_cutoff_*` on 19.0. What Community has — the accrual accounts and the manual
"Change Period" tool — is what the screen exposes. An automatic engine would be a new native module (needs approval).
Why it is under Settings: Odoo's own Configuration menu needs `account.group_account_manager`, which no NCollection role holds
(only the provisioned admin has it by accident); granting it to Owner is a ROLE_MATRIX decision and is **not** made here.

## 4. Exclude Zero Balances

One mixin (`ncollection.account.report.zero`), inherited only by reports where an all-zero row has a meaning. **The rule: a row is
dropped only when EVERY amount column is zero** — activity is never hidden; totals, section subtotals and hierarchy headers never change;
with the option off nothing changes.

| Report | A row is dropped when | Applies |
|---|---|---|
| Trial Balance | opening, debit, credit and closing are all 0 | yes |
| General Ledger | no opening balance and no movement in the period | yes |
| Partner Ledger / Customer & Vendor Statement | no opening balance and no movement | yes |
| Aged Receivable / Payable | every ageing bucket is 0 (a +100 current / −100 overdue pair is kept) | yes |
| Balance Sheet / Profit & Loss | current and previous are both 0 (account rows only; headers and totals stay) | yes |
| Revenue / Expense analysis, Cost/Profit centre/Department | an account / centre with no figure in either period | yes |
| Account balances | debit, credit and balance all 0 | yes |
| Cash Flow, UAE VAT 201, Financial Summary, Profitability, Asset Register, Equity changes, Budget vs Actual | fixed statement layouts / regulatory forms that must print every line and reconcile | **no** |

It flows through the one payload: viewer (`nc_report_run`), PDF (`_nc_print_payload`) and XLSX (`_nc_build_xlsx`) all read
`_nc_compute_lines`, and the printed filter echo states "Exclude Zero Balances: Yes". The OCA Trial Balance adapter maps its own
`hide_zero` (default on, pinned by its tests) and shows the same label.

## 5. Summary and the shared card system

Summary is each company's executive home: KPI cards (Cash & Bank, Receivables, Payables, Sales, **Expenses, Net Profit**,
open quotations, purchases, stock on hand, pending transfers, activities), the Balance Sheet and Profit & Loss from the report engine
(one `_nc_compute_lines` pass each; Expenses/Net Profit are read off the P&L rows already computed), and charts.
Every figure with a source is clickable: statement amounts open the journal items of exactly the accounts they are made of through
`ncollection.report.service.nc_report_drill` (access-checked on the run, the company and the user's own ACL); operational cards open the
SAME domain the figure counts (`drill_kpi`, server-side table, role + model gated). The report viewer's account amounts use the same call.
All cards are the one design system in `ncollection_branding` (`NcKpiCard`, `NcStatementCard`, `.nc-card`, `.nc-kpi-grid`): states
loading / loaded / zero / empty / no access / error, semantic tone only, interactive cards are real buttons with a focus ring, RTL-safe.
The platform Summary and the Home launcher use the same card surface. New SCSS must never use bare `min()/max()/clamp()` with mixed
units (R-033): wrap them in `unquote("...")`.
