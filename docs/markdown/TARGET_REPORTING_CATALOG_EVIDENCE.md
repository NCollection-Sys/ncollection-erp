# Reporting exposure — final evidence pass

Date: 2026-10-10 · Companion to [TARGET_REPORTING_CATALOG.md](TARGET_REPORTING_CATALOG.md) ·
**Evidence only. Nothing was implemented, no code, menu, security or module was changed, nothing pushed.**
Every action id, menu id, view list and group below was read from the running system (tenant `atlas`) in this pass.

**Read this first — the 18 are fewer than they look.** They are 18 *report names*; several are the same native screen with a
filter. Counting distinct hub cards: Quotations, Orders, Orders to Invoice, Customers, Purchase Orders (also serves
"Confirmed" and "Outstanding"), RFQ, Vendors, Purchase Order Lines, Receipts, Deliveries, Internal, Physical Inventory,
Replenishment and one stock-quant card (serves both Stock by Warehouse and Stock by Location) = **14 cards**, of which
Internal and the quant card are conditional on the multi-location setting, and 1 item (Sales by Sales Team) is recommended HIDE.

## 1. The 18 "Available but not exposed" items

| # | Report / capability | Odoo module | Model | Action (xml id) | Current menu (xml id) | Current view type | Available views | Current security group | NCollection roles that should see it | In the Reports hub today? | Why not exposed | Recommended action | PDF needed? | XLSX needed? |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Quotations | sale | sale.order | sale.action_quotations_with_onboarding | Sales ▸ Orders ▸ Quotations (sale.menu_sale_quotations) | List | list, kanban, form, calendar, pivot, graph, activity | menu: Sales / User: Own Documents Only | Sales (own), Manager (all), CEO, Owner | No | Operational menu stayed in the Sales app when analysis menus were re-filed; hub never listed it | EXPOSE NATIVE | NO (native per-quotation PDF exists) | YES |
| 2 | Sales Orders (confirmed) | sale | sale.order | sale.action_orders | Sales ▸ Orders ▸ Orders (sale.menu_sale_order) | List | list, kanban, form, calendar, pivot, graph, activity | menu: Sales / User: Own Documents Only | Sales (own), Manager, CEO, Owner | No | as above | EXPOSE NATIVE (printable register later = WRAP, separate approval) | YES (register) | YES |
| 3 | Outstanding Sales Orders | sale | sale.order | sale.action_orders_to_invoice (+ sale.action_orders_upselling) | Sales ▸ To Invoice ▸ Orders to Invoice (sale.menu_sale_order_invoice) | List | list, form, calendar, graph, pivot, kanban, activity | menu: Sales / User: Own Documents Only | Sales, Manager, CEO, Owner | No | as above | EXPOSE NATIVE | YES (register) | YES |
| 4 | Customer Order History | sale | sale.order | sale.act_res_partner_2_sale_order (partner smart button) | Customer form ▸ Sales; Sales ▸ Orders ▸ Customers (sale.res_partner_menu) | List | list, kanban, form, graph | menu: Sales / User: Own Documents Only | Sales, Manager, CEO, Owner | No | Reached from the customer record only | EXPOSE NATIVE (the Customers card; no new action) | NO | YES |
| 5 | Sales by Sales Team | sale | sale.report | sale.action_order_report_so_salesteam / _quotation_salesteam | none (opened from the Sales Teams kanban) | Graph | graph, list | none on the action (ORM: Sales "own documents" rule) | Manager, CEO, Owner | No | No menu; Sales Team is already a group-by in Sales Analysis | HIDE (no card; group-by is enough) | NO | YES (pivot) |
| 6 | Purchase Orders | purchase | purchase.order | purchase.purchase_form_action | Purchase ▸ Orders ▸ Purchase Orders (purchase.menu_purchase_form_action) | List | list, kanban, form, pivot, graph, calendar, activity | app menu: Purchase / User, Administrator | Purchase user, Purchase manager (no NCollection role yet — D-1); then Owner, CEO, Manager | No | Not in the hub; and the Purchase app is invisible to every NCollection role (no Purchase group) | EXPOSE NATIVE after D-1 | YES (register) | YES |
| 7 | RFQs | purchase | purchase.order | purchase.purchase_rfq | Purchase ▸ Orders ▸ Requests for Quotation (purchase.menu_purchase_rfq) | List | list, kanban, form, pivot, graph, calendar, activity | app menu: Purchase / User, Administrator | Purchase user/manager | No | as above | EXPOSE NATIVE after D-1 | NO (native per-RFQ PDF) | YES |
| 8 | Confirmed Purchase Orders | purchase | purchase.order | purchase.purchase_form_action (domain state = purchase) | same menu as Purchase Orders | List | as Purchase Orders | app menu: Purchase / User, Administrator | Purchase user/manager | No | Same screen as Purchase Orders (filter, not a separate report) | EXPOSE NATIVE (one card covers both) | YES (register) | YES |
| 9 | Outstanding Purchase Orders | purchase | purchase.order | purchase.purchase_form_action (filters Late Receipts, Waiting Bills) | same menu | List | as Purchase Orders | app menu: Purchase / User, Administrator | Purchase user/manager | No | Same screen; saved filters only | EXPOSE NATIVE (one card, filters preset) | YES (register) | YES |
| 10 | Vendor Order History | purchase | purchase.order | purchase.act_res_partner_2_purchase_order (partner smart button) | Vendor form ▸ Purchase; Purchase ▸ Orders ▸ Vendors (purchase.menu_procurement_management_supplier_name) | List | list, kanban, form, graph | app menu: Purchase / User, Administrator | Purchase user/manager | No | Reached from the vendor record only | EXPOSE NATIVE after D-1 | NO | YES |
| 11 | Purchase Order Lines | purchase | purchase.order.line | purchase.action_purchase_history | NONE (0 menus; product smart button only) | List | list, pivot, graph | none on the action (ORM: Purchase User / Administrator) | Purchase user/manager | No | Native action exists with no menu at all | EXPOSE NATIVE after D-1 | NO | YES |
| 12 | Receipts | stock | stock.picking | stock.method_action_picking_tree_incoming → stock.action_picking_tree_incoming | Inventory ▸ Operations ▸ Transfers ▸ Receipts (stock.in_picking) | Server action → List | list, kanban, form, calendar, activity | app menu: Inventory / User, Administrator | Warehouse, Manager, CEO, Owner | No | Operations menu stayed in the Inventory app | EXPOSE NATIVE | NO (native Delivery Slip / Picking Operations / Reception Report per transfer) | YES |
| 13 | Deliveries | stock | stock.picking | stock.method_action_picking_tree_outgoing → stock.action_picking_tree_outgoing | Inventory ▸ Operations ▸ Transfers ▸ Deliveries (stock.out_picking) | Server action → List | list, kanban, form, calendar, activity | app menu: Inventory / User, Administrator | Warehouse, Manager, CEO, Owner | No | as above | EXPOSE NATIVE | NO (native per-transfer PDFs) | YES |
| 14 | Internal Transfers | stock | stock.picking | stock.method_action_picking_tree_internal → stock.action_picking_tree_internal | Inventory ▸ Operations ▸ Transfers ▸ Internal (stock.int_picking) | Server action → List | list, kanban, form, calendar | menu: Manage Multiple Stock Locations (setting) + Inventory app groups | Warehouse, Manager, CEO, Owner — only when the setting is on | No | Gated on the multi-location setting (off) | EXPOSE NATIVE when multi-location is on | NO | YES |
| 15 | Inventory Adjustments | stock | stock.quant (+ stock.move.line history) | stock.action_view_inventory_tree (server) | Inventory ▸ Operations ▸ Adjustments ▸ Physical Inventory (stock.menu_action_inventory_tree) | Server action → List | list (quant); history: stock.move.line list, kanban, pivot | app menu: Inventory / User, Administrator | Warehouse, Manager, CEO, Owner | No (history = Moves History card, which is) | Operations menu stayed in the Inventory app | EXPOSE NATIVE | YES (native Count Sheet already exists) | YES |
| 16 | Replenishment / Reordering | stock | stock.warehouse.orderpoint | stock.action_replenishment (server); stock.action_orderpoint has no menu | Inventory ▸ Operations ▸ Procurement ▸ Replenishment (stock.menu_reordering_rules_replenish) | Server action → List | list, kanban, form | menu: Inventory / Administrator | Inventory manager (no NCollection role yet — D-1b) | No | Gated on Inventory Administrator, which no role holds | EXPOSE NATIVE after D-1b | NO | YES |
| 17 | Stock by Warehouse | stock | stock.quant (`warehouse_id`) | stock.stock_quant_action | NONE (0 menus) | List | list, form (model also has pivot + graph views) | none on the action (ORM: Inventory User) | Warehouse, Manager, CEO, Owner | No | Quant action has no menu of its own | EXPOSE NATIVE (card grouped by warehouse) | NO | YES |
| 18 | Stock by Location | stock | stock.quant | stock.stock_quant_action (via server action on menu stock.menu_valuation) | Inventory ▸ Reporting ▸ "Inventory Valuation" (stock.menu_valuation) | Server action → List | list, form (+ pivot, graph on the model) | menu: Manage Multiple Stock Locations / Technical Features / Manage Different Stock Owners | Warehouse, Manager, CEO, Owner — when multi-location is on | No (catalog skipped it: menu hidden) | Gated on the multi-location setting; the hub catalog only lists visible menus | EXPOSE NATIVE when multi-location is on | NO | YES |

**Reading the table**

* **Why none of them is in the hub:** the hub catalog was built from the native *Reporting* menus only. Operational
  menus (Orders, RFQ, Transfers, Adjustments, Procurement) belong to their apps and were never catalogued; three
  of them have an action with no menu at all (rows 5, 11, 17) and one more is reached only through a menu gated on a setting (row 18).
* **Security — what exposure would and would not change.** A hub card is shown only if the user's menu groups pass **and**
  the user can read the model (already enforced in `nc_report_catalog`). So exposing a card never widens ORM access.
  The ORM read rights found: `sale.order` — Sales "own documents" (+ Invoicing / Accounting groups);
  `purchase.order` and `.line` — Purchase User/Administrator (+ Invoicing / Accounting groups);
  `stock.picking` — Inventory User/Administrator (+ Invoicing / Accounting); `stock.quant` — Inventory User and the
  NCollection "Role / User" (employee) group; `stock.warehouse.orderpoint` — Inventory User/Administrator.
* **Roles that cannot see anything today:** all Purchase rows (rows 6–11) and Replenishment (row 16) — no NCollection role holds
  the Odoo group; this is D-1 / D-1b and is the one thing Step 2 would have to settle before Step 1 can show those cards.
* **Recommended-action count:** 0 EXPOSE NATIVE (5 of them only after D-1/D-1b or when multi-location is enabled),
  1 HIDE, **0 WRAP, 0 ADAPTER, 0 CUSTOM**. Printable registers (rows 2, 3, 6, 8, 9, 15) would be a *separate* WRAP
  decision; they are listed with PDF = YES but are not needed to expose the native screen.
* **Distinct native actions behind the 18:** sale.order (5 menus/actions), purchase.order (2 menus + 1 shared card + 1 smart button), stock.picking (3), stock.quant (3), purchase.order.line (1), stock.warehouse.orderpoint (1), sale.report team actions (1).

## 2. The 6 "Partial" items

| Item | What currently exists | What is missing | Can Community cover the missing part? | Recommended final implementation |
|---|---|---|---|---|
| Sales Returns / Credit Notes | Invoice Analysis with the "Credit Notes" filter (hub, accounting roles); return transfers and the native "Return slip" PDF in Inventory | One view joining returned goods (stock) with credit notes (money); Sales roles cannot read Invoice Analysis (ORM: Invoicing / Accounting only) | Money side: yes. Goods side: yes. A single combined view: no (two models) | EXPOSE NATIVE two cards (Credit Notes for accounting roles; returns via the Transfers card). No combined report — do not build one |
| Vendor Cost Analysis | Purchase Analysis measure "Average Cost"; Unit Cost History list (stock_account.stock_avco_report_action) exists | The cost-history list has no menu; cost *trend per vendor* is only a pivot the user must assemble | Yes — both pieces exist natively | EXPOSE NATIVE a card for Unit Cost History; keep the pivot measure. No new report |
| Purchase Returns | Return transfers (stock) and vendor refunds in Vendor Bill Analysis (hub, added 2026-10-09) | No single "purchase returns" view; purchase.report has no return flag | Each half yes; combined no | EXPOSE NATIVE (existing cards); no combined report |
| Reserved Stock | Product list "Free To Use" (hub); per-location reserved / available on the quant list | Per-location reservation sits in the unmenued quant action | Yes — stock.quant has Reserved Quantity and Available Quantity | EXPOSE NATIVE with Stock by Location (same action, one card) |
| Inventory Valuation | Product list "Total Value" (hub); stock.quant "Value"; per-move valuation list (stock_account.stock_move_valuation_action) with no menu | Per-move valuation not reachable; **no as-of-date valuation screen found among the inspected actions**; no printable valuation | Current value per product: yes. Per-move list: yes (needs a menu). As-of-date and PDF: not found in Community | EXPOSE NATIVE the per-move valuation list; **WRAP/CUSTOM only for a printable valuation** on the report engine — separate approval |
| Forecasted Inventory | Product list "Forecasted" (hub); forecast report as a button on each product form; Replenishment | A forecast view reachable without opening each product; Replenishment is Administrator-gated and no role holds it | Yes — native, but the menu is gated | EXPOSE NATIVE Replenishment after D-1b; per-product forecast stays as is |

## 3. The 5 genuinely missing from Community

| Capability | Exact business requirement | Why native Community cannot cover it | Can OCA cover it? | Custom NCollection development? | Priority |
|---|---|---|---|---|---|
| Sales vs Target | Compare invoiced revenue against a quota per salesperson and per team for a period, with attainment % | The only native target is **one field on the sales team** (`crm.team.invoiced_target`, "Invoicing Target", with "Invoiced This Month") shown on a kanban card. There is no per-salesperson target (`res.users` has none) and **no report** — `sale.report` has no target field | Yes in principle: `sales_team_invoiced_target_report` (OCA/sale-reporting 19.0, AGPL, depends sale+crm). Its repo is **not pinned** and it was read from its manifest only — approval needed | Not if the OCA module fits; otherwise a small SQL report | ADVANCED |
| Purchase Price Variance | Show, per product/vendor, how the price on received purchase lines differs from the standard cost or the previous purchase price | purchase.report has "Average Cost" and the product has a standard cost, but **no report compares them** | No — not in the pinned repos; the unpinned purchase-reporting repo has vendor-grouped order printouts, tax and shipping-address options, not variance | Yes — small custom report on the existing engine (S–M) | ADVANCED |
| Stock Aging | On-hand quantity and value by age bucket (e.g. 0–30 / 31–60 / 61–90 / 90+ days) per product, location and category, printable | The data exists (`stock.quant.in_date`, "Incoming Date") but **no report, view or filter ages it** | No — stock-logistics-reporting 19.0 offers value-per-move, delivery notes and demand info, not aging | Yes — a new report; it needs `stock`, so it cannot live in the finance module (new module, approval) | SHOULD |
| Slow-Moving Inventory | Products with low movement over a period, ranked, with on-hand value | No report. Derivable from the last done move per product (`stock.move.line`) but nothing computes it | No | Yes — second view of the Stock Aging report (S after aging) | ADVANCED |
| Dead Stock | Products with stock on hand and **no movement** for N days | No report | No | Yes — a variant of Slow-Moving (same engine) | ADVANCED |

(Sales Margin is the one *additional-module* item: Community provides it through the core module `sale_margin`, which is not
installed; installing it changes module state and needs approval. It is listed in group 4 below.)

## 4. The exact list of the 64 audited capabilities

**Counts:** Already exposed 34 · Available but not exposed 18 · Partial 6 ·
Requires additional module 1 · Missing from Community 5 · **Total 64**.

### 4.1 Already exposed (34) — hub card or dashboard card

1. Sales Order Lines *(Sales)*
2. Confirmed Sales *(Sales)*
3. Sales by Customer *(Sales)*
4. Sales by Product *(Sales)*
5. Sales by Salesperson *(Sales)*
6. Sales by Product Category *(Sales)*
7. Sales by Date *(Sales)*
8. Delivered Sales *(Sales)*
9. Invoiced Sales *(Sales)*
10. Cancelled Sales *(Sales)*
11. Top Customers *(Sales)*
12. Top Products *(Sales)*
13. Salesperson Leaderboard *(Sales)*
14. Sales order status analysis *(Sales)*
15. Purchases by Vendor *(Purchases)*
16. Purchases by Product *(Purchases)*
17. Purchases by Buyer *(Purchases)*
18. Purchases by Date *(Purchases)*
19. Vendor Performance *(Purchases)*
20. Days to Confirm *(Purchases)*
21. Days to Receive *(Purchases)*
22. Received vs Ordered *(Purchases)*
23. Vendor Bills *(Purchases)*
24. Cancelled Purchases *(Purchases)*
25. Vendor Payment History *(Purchases)*
26. Stock Report / Stock On Hand *(Inventory)*
27. Stock by Product *(Inventory)*
28. Stock by Category *(Inventory)*
29. Incoming Stock *(Inventory)*
30. Outgoing Stock *(Inventory)*
31. Inventory Moves History *(Inventory)*
32. Inventory Moves Analysis *(Inventory)*
33. Scrap Report *(Inventory)*
34. Inventory Turnover *(Inventory)*

### 4.2 Available but not exposed (18)

1. Quotations *(Sales)*
2. Sales Orders (confirmed) *(Sales)*
3. Sales by Sales Team *(Sales)*
4. Customer Order History *(Sales)*
5. Outstanding Sales Orders *(Sales)*
6. Purchase Orders *(Purchases)*
7. Purchase Order Lines *(Purchases)*
8. RFQs *(Purchases)*
9. Confirmed Purchase Orders *(Purchases)*
10. Vendor Order History *(Purchases)*
11. Outstanding Purchase Orders *(Purchases)*
12. Stock by Warehouse *(Inventory)*
13. Stock by Location *(Inventory)*
14. Receipts *(Inventory)*
15. Deliveries *(Inventory)*
16. Internal Transfers *(Inventory)*
17. Inventory Adjustments *(Inventory)*
18. Reordering / Replenishment Analysis *(Inventory)*

### 4.3 Partial (6)

1. Sales Returns / Credit Notes *(Sales)*
2. Vendor Cost Analysis *(Purchases)*
3. Purchase Returns *(Purchases)*
4. Reserved Stock *(Inventory)*
5. Inventory Valuation *(Inventory)*
6. Forecasted Inventory *(Inventory)*

### 4.4 Requires additional module (1)

1. Sales Margin / Profitability *(Sales)*  — core Odoo module `sale_margin` (not installed)

### 4.5 Missing from Community (5)

1. Sales vs Target *(Sales)*
2. Purchase Price Variance *(Purchases)*
3. Stock Aging *(Inventory)*
4. Slow-Moving Inventory *(Inventory)*
5. Dead Stock *(Inventory)*

## 5. What approval of Step 1 and Step 2 would actually change

| Step | What it does | What it does **not** do |
|---|---|---|
| **Step 1 — expose** | Hub cards / menus pointing at the *same* native actions listed in §1 (and §2's unmenued ones). Reuses the existing `_NATIVE_REPORT_MENUS` mechanism (idempotent, created only when the owning module is installed, group = the one that already holds the ORM right) | No new report code, no ACL change, no copy of an action, no PDF engine |
| **Step 2 — roles (D-1, D-1b)** | Decide who is a Purchase user/manager and an Inventory manager. This is what makes rows 6–11 and 16 reachable by anyone | Not required for the Sales and Inventory-user rows, which Step 1 can serve immediately |

Suggested split if you approve in parts: **Step 1a** (Sales rows 1–4, Inventory rows 12–15 and 17–18; row 5 is HIDE) needs no
role decision; **Step 1b** (Purchase rows 6–11 and Replenishment) waits for Step 2.
