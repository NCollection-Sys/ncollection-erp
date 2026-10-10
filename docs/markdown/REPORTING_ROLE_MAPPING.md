# Reporting role mapping — NCollection role → Odoo groups → apps → menus → models → capabilities

Date: 2026-10-10 · Step 2 of the exposure plan. Every cell below was **probed on the running system** (a real user per persona,
on two tenants: results identical; and on the platform database), not read from the matrix document.
Related: `docs/ROLE_MATRIX.md` (D10), [TARGET_REPORTING_CATALOG_EVIDENCE.md](TARGET_REPORTING_CATALOG_EVIDENCE.md).

## 1. Rule followed

* **Reuse the existing roles** (Employee, Sales, Manager, Warehouse, Accountant, CEO, Owner). **No new NCollection role was created.**
* Purchase user / Purchase manager / Inventory manager are **plain Odoo groups** assigned to a user by the Owner — Odoo already
  defines exactly those capabilities.
* A hub card is shown only when **both** the menu group **and** the model read right pass; a hidden menu is never the control.

## 2. Persona → role/groups → applications

| Persona | How it is assigned | Key Odoo groups held (probed) | Applications visible |
|---|---|---|---|
| Employee | NCollection role **Employee** | — | Employees, Reports |
| Sales User (Sales role) | NCollection role **Sales** | group_sale_salesman | CRM, Employees, Reports, Sales |
| Sales Manager (Manager role) | NCollection role **Manager** (all-documents sales; also Inventory User, HR User) | group_stock_user, group_sale_salesman, group_sale_salesman_all_leads | CRM, Employees, Inventory, Reports, Sales |
| Purchase User (Odoo group) | Employee role **+ Odoo group** `purchase.group_purchase_user` (assigned by the Owner; no role) | group_purchase_user | Employees, Purchase, Reports |
| Purchase Manager (Odoo group) | Employee role **+ Odoo group** `purchase.group_purchase_manager` | group_purchase_user, group_purchase_manager | Employees, Purchase, Reports |
| Inventory User (Warehouse role) | NCollection role **Warehouse** | group_stock_user | Employees, Inventory, Reports |
| Inventory Manager (Odoo group) | Employee role **+ Odoo group** `stock.group_stock_manager` | group_stock_user, group_stock_manager | Employees, Inventory, Reports |
| Accountant | NCollection role **Accountant** | group_account_readonly, group_account_user | Accounting, Employees, Reports |
| CEO | NCollection role **CEO** (Manager chain + accounting read-only) | group_stock_user, group_sale_salesman, group_sale_salesman_all_leads, group_account_readonly | Accounting, CRM, Employees, Inventory, Reports, Sales |
| Owner | NCollection role **Owner** (CEO chain + `base.group_system` + accounting user + Purchase and Inventory administration) | group_purchase_user, group_purchase_manager, group_stock_user, group_stock_manager, group_sale_salesman, group_sale_salesman_all_leads, group_account_readonly, group_account_user, group_system, group_erp_manager | Accounting, Apps, CRM, Employees, Inventory, Purchase, Reports, Sales, Settings, Workspace Settings |
| **Platform Admin** (platform database `admin`) | the platform database's own administrator; **not a tenant role** | `base.group_system`, `base.group_erp_manager`, accounting user, Purchase manager, Inventory manager, Sales manager | Accounting, Purchase, Inventory, Sales, CRM, Settings, Apps, NCollection SaaS, Reseller, Job Queue (+ the usual) |

## 3. Persona → operational reports → models (ORM rights probed) → accounting

ORM rights read as **R**ead **W**rite **C**reate **U**nlink-delete (`D`) per model, in the order
`sale.order · purchase.order · stock.picking · stock.quant · stock.warehouse.orderpoint · account.move`.

| Persona | Operational cards in the hub | ORM rights (order above) | Accounting reports kept |
|---|---|---|---|
| Employee | — (none) | ---- · ---- · ---- · R--- · ---- · ---- | — |
| Sales User (Sales role) | Orders to Invoice, Quotations, Sales Order Lines, Sales Orders | RWC- · ---- · RWC- · R--- · R--- · R--- | — |
| Sales Manager (Manager role) | Deliveries, Inventory Adjustments, Orders to Invoice, Quotations, Receipts, Sales Order Lines, Sales Orders, Stock by Location | RWC- · R--- · RWCD · RWC- · R--- · R--- | — |
| Purchase User (Odoo group) | Purchase Order Lines, Purchase Orders, Requests for Quotation | ---- · RWCD · RWCD · R--- · R--- · RWCD | — |
| Purchase Manager (Odoo group) | Purchase Order Lines, Purchase Orders, Requests for Quotation | ---- · RWCD · RWCD · R--- · R--- · RWCD | — |
| Inventory User (Warehouse role) | Deliveries, Inventory Adjustments, Receipts, Stock by Location | RW-- · R--- · RWCD · RWC- · R--- · ---- | — |
| Inventory Manager (Odoo group) | Deliveries, Inventory Adjustments, Receipts, Replenishment, Stock by Location | RW-- · R--- · RWCD · RWC- · RWCD · ---- | — |
| Accountant | — (none) | RW-- · RW-- · RWC- · R--- · ---- · RWCD | Customer Payments, General Ledger, Invoice Analysis, Tax Analysis, Trial Balance, Vendor Payments |
| CEO | Deliveries, Inventory Adjustments, Orders to Invoice, Quotations, Receipts, Sales Order Lines, Sales Orders, Stock by Location | RWC- · R--- · RWCD · RWC- · R--- · R--- | Customer Payments, General Ledger, Invoice Analysis, Tax Analysis, Trial Balance, Vendor Payments |
| Owner | Deliveries, Inventory Adjustments, Orders to Invoice, Purchase Order Lines, Purchase Orders, Quotations, Receipts, Replenishment, Requests for Quotation, Sales Order Lines, Sales Orders, Stock by Location | RWC- · RWCD · RWCD · RWC- · RWCD · RWCD | Customer Payments, General Ledger, Invoice Analysis, Tax Analysis, Trial Balance, Vendor Payments |
| **Platform Admin** | all 12 (on the platform's own data) | full | all |

## 4. What changed in the role links (D10) — and the attempt that was withdrawn

**Kept:** Owner gains `purchase.group_purchase_manager` and `stock.group_stock_manager`. Verified from Odoo's group data that
neither purchase group implies any accounting group and `stock.group_stock_manager` implies only `stock.group_stock_user`;
Owner already held `account.group_account_user` and `base.group_system`, so **no right is gained that Owner lacked.**

**Tried and withdrawn:** linking `purchase.group_purchase_user` to the **Manager** role (so Manager/CEO/Owner would see Purchase).
The live ORM probe showed it gave Manager — and through the chain the **CEO** — create/write/delete on `account.move`
(limited by the record rule "Purchase User Account Move" to vendor bills/refunds/receipts). That is a financial write, which
D5 denies Manager and D2 denies CEO ("read-only financials"). It was removed before commit; dev databases that had synced it were
corrected; a test now fails if Manager or CEO ever holds a Purchase group or an `account.move` write/create/unlink right.

**Consequence:** Manager and CEO do **not** see Purchase reports. Giving them read-only access would need new ACL rows
(the D9 pattern) — left as an explicit decision, not invented here.

## 5. Security verification for every newly exposed report

The eight requirements, per card. Column "Direct RPC": the same user calling the model directly gets the ORM answer.

| Card | Action reused | Model | Menu group (= least group that already holds the ORM read) | Record rules in force | Menu visibility | Company / tenant isolation | Drill-down | Direct RPC |
|---|---|---|---|---|---|---|---|---|
| Quotations | sale.action_quotations_with_onboarding | sale.order | sales_team.group_sale_salesman | Personal Orders (own) / All Orders (all) / company | hub card shown only if menu group AND model read pass (`nc_report_catalog`) | company rule on the model; DB-per-tenant (one registry per database) | a row opens the native form: same ACL + record rule as the list | direct `search`/`read` as the same user hits the ORM ACL — tested |
| Sales Orders | sale.action_orders | sale.order | sales_team.group_sale_salesman | as above | hub card shown only if menu group AND model read pass (`nc_report_catalog`) | company rule on the model; DB-per-tenant (one registry per database) | a row opens the native form: same ACL + record rule as the list | direct `search`/`read` as the same user hits the ORM ACL — tested |
| Orders to Invoice | sale.action_orders_to_invoice | sale.order | sales_team.group_sale_salesman | as above | hub card shown only if menu group AND model read pass (`nc_report_catalog`) | company rule on the model; DB-per-tenant (one registry per database) | a row opens the native form: same ACL + record rule as the list | direct `search`/`read` as the same user hits the ORM ACL — tested |
| Sales Order Lines (list-first) | sale.report_all_channels_sales_action | sale.report | sales_team.group_sale_salesman | Personal / All Orders Analysis / company | hub card shown only if menu group AND model read pass (`nc_report_catalog`) | company rule on the model; DB-per-tenant (one registry per database) | a row opens the native form: same ACL + record rule as the list | direct `search`/`read` as the same user hits the ORM ACL — tested |
| Purchase Orders | purchase.purchase_form_action | purchase.order | purchase.group_purchase_user | company (global rule only) | hub card shown only if menu group AND model read pass (`nc_report_catalog`) | company rule on the model; DB-per-tenant (one registry per database) | a row opens the native form: same ACL + record rule as the list | direct `search`/`read` as the same user hits the ORM ACL — tested |
| Requests for Quotation | purchase.purchase_rfq | purchase.order | purchase.group_purchase_user | company | hub card shown only if menu group AND model read pass (`nc_report_catalog`) | company rule on the model; DB-per-tenant (one registry per database) | a row opens the native form: same ACL + record rule as the list | direct `search`/`read` as the same user hits the ORM ACL — tested |
| Purchase Order Lines | purchase.action_purchase_history | purchase.order.line | purchase.group_purchase_user | company | hub card shown only if menu group AND model read pass (`nc_report_catalog`) | company rule on the model; DB-per-tenant (one registry per database) | a row opens the native form: same ACL + record rule as the list | direct `search`/`read` as the same user hits the ORM ACL — tested |
| Receipts | stock.method_action_picking_tree_incoming (server) | stock.picking | stock.group_stock_user | company | hub card shown only if menu group AND model read pass (`nc_report_catalog`) | company rule on the model; DB-per-tenant (one registry per database) | a row opens the native form: same ACL + record rule as the list | direct `search`/`read` as the same user hits the ORM ACL — tested |
| Deliveries | stock.method_action_picking_tree_outgoing (server) | stock.picking | stock.group_stock_user | company | hub card shown only if menu group AND model read pass (`nc_report_catalog`) | company rule on the model; DB-per-tenant (one registry per database) | a row opens the native form: same ACL + record rule as the list | direct `search`/`read` as the same user hits the ORM ACL — tested |
| Stock by Location | stock.action_view_quants (server) | stock.quant | stock.group_stock_user | company (+ no-company quants) | hub card shown only if menu group AND model read pass (`nc_report_catalog`) | company rule on the model; DB-per-tenant (one registry per database) | a row opens the native form: same ACL + record rule as the list | direct `search`/`read` as the same user hits the ORM ACL — tested |
| Inventory Adjustments | stock.action_view_inventory_tree (server; native group Inventory / User) | stock.quant | stock.group_stock_user | company | hub card shown only if menu group AND model read pass (`nc_report_catalog`) | company rule on the model; DB-per-tenant (one registry per database) | a row opens the native form: same ACL + record rule as the list | direct `search`/`read` as the same user hits the ORM ACL — tested |
| Replenishment | stock.action_replenishment (server) | stock.warehouse.orderpoint | stock.group_stock_manager | company | hub card shown only if menu group AND model read pass (`nc_report_catalog`) | company rule on the model; DB-per-tenant (one registry per database) | a row opens the native form: same ACL + record rule as the list | direct `search`/`read` as the same user hits the ORM ACL — tested |

**Where each requirement is proven**

| # | Requirement | Proof |
|---|---|---|
| 1 | Menu visibility | `test_hub_roles.test_each_persona_sees_exactly_the_operational_reports_it_is_meant_to` (10 personas) + real browser per persona on two tenants (143 checks each) |
| 2 | Action access | actions reused unchanged; server actions checked against their model in `_nc_can_open`; `stock.action_view_inventory_tree` keeps its native group |
| 3 | Model ACL | `test_unauthorised_personas_are_refused_by_the_orm_not_just_the_menu` |
| 4 | Record rules | `test_a_salesperson_sees_only_own_orders_and_a_manager_sees_all` |
| 5 | Company isolation | `test_orders_of_another_company_are_invisible_to_a_user_who_cannot_use_it` (sale + purchase) |
| 6 | Tenant isolation | DB-per-tenant; re-run on this code: own-database figures only, no platform/other-tenant data, cross-database login refused (57 checks) |
| 7 | Drill-down | `test_drill_down_is_the_native_record_read_under_the_same_rules` |
| 8 | Direct RPC | the ORM assertions above; financial reports and OCA ledger wizards still refused to every operational persona |

## 6. Native behaviours that exist today and were **not** changed (flagged, not fixed)

These are Odoo core defaults found while probing; none is widened by this work, and each is a candidate for the security decisions D-2/D-3.

* **Employee** (base user) has full create/write/delete on `stock.move.line` (core `access_stock_move_line_all`).
* **Inventory users** can read **and write** `sale.order` and read every purchase order in the company; **purchase users** can
  read and write transfers (`stock.picking`) and write **vendor bills** (record-rule limited to bills).
* **Sales** users can read `account.move` / `account.move.line` (customer invoices own/all documents).
* Inventory and purchase records carry **only the company rule** — there is no per-user "own documents" rule, so any Inventory/Purchase user sees every receipt / PO of the company.

## 7. Not exposed, and why

| Item | Reason |
|---|---|
| Sales by Sales Team | HIDE — already a group-by in Sales Analysis; the team actions have no menu by design |
| Customer / Vendor Order History as separate cards | Served by the Sales Orders / Purchase Orders cards (group by customer / vendor); the native partner smart button remains |
| Internal Transfers | Native menu is gated on the multi-location setting; an empty card for tenants not using it is noise (stays in the Inventory app) |
| Purchase cards for Manager / CEO | Would require a purchase group (financial write, above) or new read-only ACLs — decision pending |
| Inventory Valuation, aging, margin, targets, registers, price variance | Separate approved-gap phase |
