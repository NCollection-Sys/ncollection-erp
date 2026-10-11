# Target reporting catalog

Date: 2026-10-10 · **Analysis only — no product code was changed for this document.**
Companions: [ACCOUNTING_REPORTING_CAPABILITY_MATRIX.md](ACCOUNTING_REPORTING_CAPABILITY_MATRIX.md) ·
[REPORTING_ARCHITECTURE_RECOMMENDATION.md](REPORTING_ARCHITECTURE_RECOMMENDATION.md)

## Operational Reporting — Sales / Purchases / Inventory

### 1. The direct answer

> **"Why am I seeing mostly charts and analysis instead of complete operational reports for Sales, Purchases and Inventory?"**

**A combination — and Odoo Community is the smallest part of it.** Traced from the running system (menus, actions,
models, groups), not guessed:

| # | Cause (your list) | Evidence | Weight |
|---|---|---|---|
| 2 | **NCollection menu exposure** | When the native *Reporting* menus were re-filed into the Reports hub, only the **analysis** actions (pivot/graph over `sale.report`, `purchase.report`, stock moves) were catalogued. The **operational** screens — Quotations, Sales Orders, RFQs, Purchase Orders, Receipts, Deliveries, Scrap, Physical Inventory — live in the Sales / Purchase / Inventory *app* menus and were never in the hub. Three more native actions have **no menu at all** (`purchase.order.line` history, the quant "Locations" list, per-move stock valuation) | **Largest** |
| 3 | **NCollection role / security** | The analysis menus were gated on Odoo's *Administrator* groups that no NCollection role has — the hub's Sales / Purchases / Inventory categories were **empty for every role, Owner and CEO included** (fixed for Sales and Inventory on 2026-10-09). **No role holds a Purchase group**, so the whole Purchase app is invisible to Owner / CEO / Manager (D-1). There is no Inventory-manager role, so Replenishment and valuation-style screens stay unreachable (D-1b) | **Large** |
| 6 | **Incorrect categorization** | The hub shows each analysis action on its default tab — Sales Analysis opens on the **graph**, Purchase Analysis on graph/pivot — so even a list-capable report reads as a chart | Medium |
| 1 | **Odoo Community limitation — real but narrow** | Community has **no aggregate printable report** for any of the three areas: PDF exists only **per document** (quotation, PO, RFQ, delivery slip, picking operations, reception report, count sheet, lot label, product label, pricelist). There is no printable "Sales Order Register", "Purchase Order Register", "Stock on Hand" or "Valuation" | Medium |
| 5 | **Missing custom operational reports** | Genuinely absent from Community: stock aging, slow-moving, dead stock, purchase price variance, sales vs target (O) | Small (5 items) |
| 4 | NCollection redesign *accidentally hiding native reports* | Yes, in the sense of 2 and 3: re-filing a menu under a new parent silently changes who sees it, and operational menus were simply left behind. Nothing was deleted | folded into 2 + 3 |

So: **the operational reports mostly exist in Odoo Community and were not exposed**, not rebuilt badly. Of 64 items
audited, 34 are already exposed (hub or dashboard), **18 are AVAILABLE BUT NOT EXPOSED**, 6 are partial,
1 need a core module that is not installed, and only **5 are not in Community at all**.
The part that *is* a Community limitation — no printable aggregate reports — is a real product gap and is addressed in §7.

### 2. Real report vs analytics (the four classes)

| Class | What it is | Typical native form | Example | Is it enough? |
|---|---|---|---|---|
| **A. Operational report** | Record-level: the documents, lines or moves themselves, filterable, drillable to the source, exportable, printable where useful | List view of `sale.order`, `purchase.order`, `stock.picking`, `stock.move.line`, `stock.quant` (+ per-document PDF) | "Sales Orders", "Receipts", "Stock on Hand" | The thing you said is missing from the hub |
| **B. Analytical report** | Aggregates sliced by dimensions | Pivot / graph over `sale.report`, `purchase.report`, `stock.move` | "Sales by Customer" | Useful; **does not replace A** |
| **C. Management dashboard** | KPIs and cards for a role | NCollection dashboards (Finance, CEO, Sales, Warehouse) | "Top Customers", "Inventory Turnover" | A summary, not a report |
| **D. Chart** | One visual | graph view | "Sales by Month" | Never a report |

A chart of Sales by Month is **not** a Sales Order Report; a Purchase Amount by Vendor chart is **not** a Purchase Detail
Report; a stock KPI is **not** a Stock Report; and a pivot is useful but is **not** a printable operational report. The target
needs **both** the operational layer and the analytical layer.

### 3. Method, and what was and was not verified

Evidence was read from the running Odoo 19 system (tenant `atlas`: sale, purchase, stock, stock_account, purchase_stock,
crm, account installed) and the installed addon source:

* every menu under Sales / Purchase / Inventory with its action, model, view modes, domain and groups (61 menus);
* every `ir.actions.act_window` on the relevant models and **how many menus point at it** (0 = no menu);
* every `qweb` report on the relevant models (the printable documents);
* the view types and the search-view filters of each model, and the SQL `where` of `sale.report` / `purchase.report`;
* for each of 8 roles: which apps and operational screens are visible (a probe user per role, server side);
* the hub catalog per role (browser-verified earlier in the session).

**Not verified:** ¹ XLSX export of lists/pivots is a core web-client feature and was **not exercised here**; native views were
not benchmarked at volume; the OCA `sales_team_invoiced_target_report` was read from its GitHub manifest only (repo unpinned).

### 4. Odoo Community capability inventory

**What the three apps actually ship**

| Area | Operational screens (menu → model, view modes) | Analysis (model, view modes) | Printable documents (QWeb PDF) |
|---|---|---|---|
| **Sales** | Quotations, Orders, Orders to Invoice, To Upsell (`sale.order`: list, kanban, form, calendar, **pivot, graph**, activity); Customers | `sale.report` — one row **per order line**: graph, pivot, list; **no** margin field (needs `sale_margin`, not installed) | Quotation / Order, PDF Quote, Pro-forma Invoice, Invoice PDF, Pricelist |
| **Purchase** | Requests for Quotation, Purchase Orders (`purchase.order`: list, kanban, form, calendar, **pivot, graph**); Vendors | `purchase.report`: graph, pivot, list — **Days to Confirm, Days to Receive**, Qty Ordered / Received / Billed / To be Billed, Average Cost; `vendor.delay.report` (graph only); `purchase.order.line` history (list, pivot, graph — **no menu**) | Purchase Order, Request for Quotation |
| **Inventory** | Receipts, Deliveries, Internal (`stock.picking`, server-action menus); Scrap (list, form, kanban, **pivot, graph**); Physical Inventory (`stock.quant`); Replenishment | Stock Analysis (`product.product`: On Hand, Free, Forecasted, Incoming, Outgoing, Total Value); Moves History (`stock.move.line`: list, kanban, pivot); Moves Analysis (`stock.move`: pivot, list, graph); quant "Locations" (`stock.quant`: list, **pivot, graph** — action has **no menu**) ; per-move Valuation (`stock.move`, **no menu**); Unit Cost History (`stock.avco.report`, **no menu**) | Delivery Slip, Picking Operations, Reception Report, Return slip, Packages, Count Sheet, Lot/Serial (PDF + ZPL), Product Labels |

**What Community does not have (verified absent):** any aggregate printable report (register / on-hand / valuation PDF); a
`sale.order.line` screen (its role is played by `sale.report`); stock aging, slow-moving, dead stock; purchase price
variance; sales-vs-target as a report; margin in the sales analysis (core module `sale_margin` exists, is **not installed**).

**Filters and grouping are strong where they exist** (read from the search views): orders — Quotations, Sales Orders, To
Invoice, To Upsell, Late Availability; purchase orders — Late, Late Receipts, Waiting Bills, To Approve; move lines —
Done, Incoming, Outgoing, Internal, **Inventory Adjustments**, Transfers, last 30 days / 3 / 12 months; quants — Internal
Locations, Negative Stock, To Count, To Apply, Product Category; credit notes — "Credit Notes" in Invoice Analysis.

### 5. Exposure audit — "available but not exposed" (section 37)

Status of every item, by area:

| Area | Items | Exposed (hub) | Exposed (dashboard) | AVAILABLE BUT NOT EXPOSED — app only | AVAILABLE BUT NOT EXPOSED — no menu | Partial | Module not installed | Not in Community |
|---|---|---|---|---|---|---|---|---|
| SALES | 22 | 12 | 2 | 4 | 1 | 1 | 1 | 1 |
| PURCHASES | 20 | 11 | 0 | 5 | 1 | 2 | 0 | 1 |
| INVENTORY | 22 | 8 | 1 | 5 | 2 | 3 | 0 | 3 |
| **Total** | 64 | 31 | 3 | 14 | 4 | 6 | 1 | 5 |

**Why each unexposed item is unexposed (traced):**

| Reason | Items it explains | Status |
|---|---|---|
| **Re-filing left the operational menus in their apps** — the hub catalog listed *Reporting* menus only | Quotations, Orders, RFQ, Purchase Orders, Receipts, Deliveries, Internal, Physical Inventory, Replenishment | Open — fix is hub cards onto the same actions (§8) |
| **Native action exists, no menu** | Purchase Order Lines, quant "Locations", per-move Valuation, Unit Cost History, team-scoped sales actions | Open (§8) |
| **Menu gated on an Administrator group no role holds** | Sales / Purchase / Inventory Analysis | **Fixed 2026-10-09** (Sales, Inventory); Purchase still needs a role |
| **No Purchase role** (and no Inventory-manager role) | The entire Purchase app; Replenishment; valuation-style screens | Decision D-1 / D-1b |
| **Menu gated on the multi-location setting** | Stock by Location, Internal Transfers | By design — expose when the setting is on |
| **Default tab is a chart** | Sales Order Lines, purchase measures | Open — a list-first card on the same action |
| **Core module not installed** | Sales Margin (`sale_margin`) | Approval needed |
| **Not in Community** | aging, slow-moving, dead stock, price variance, sales vs target | Genuine gaps (§9) |

### 6. Operational Reporting Catalog

Legend — **Class:** Operational / Analytical / Management / Chart. **Decision** follows the preferred order *native →
exposure → wrapper/adapter → OCA → custom*: **A** expose the native report directly · **B** re-file / rebrand inside the
NCollection structure (a hub card onto the same action; nothing copied) · **C** wrap with the NCollection reporting shell ·
**D** NCollection adapter/viewer · **O** OCA · **E** custom — only where the native report genuinely cannot do the job · **F**
combine native views. **Priority:** MUST / SHOULD / ADVANCED. ¹ core export, not exercised here.

#### 6.1 SALES (22)

| # | Item | Class | In Community? | Module · model | Native screen | Capabilities | Filters / grouping (read from the views) | NCollection today | Why not exposed | Decision | Priority | Target output | Roles |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Quotations | Operational | Yes | sale · sale.order | Sales ▸ Orders ▸ Quotations | list · kanban · pivot · graph · drill ✓ · XLSX ✓¹ · PDF per document only | Quotations, My Quotations, Customer, Salesperson, Order Date, To Invoice | AVAILABLE BUT NOT EXPOSED in the hub (reachable in its app menu) | Operational lists stayed in their app when analysis menus were re-filed into the hub; the hub never listed them | B — hub card onto the SAME action; printable register only later (C) | MUST | Screen + XLSX; PDF per quotation (native "Quotation / Order") | Sales user (own), Sales manager (all) |
| 2 | Sales Orders (confirmed) | Operational | Yes | sale · sale.order | Sales ▸ Orders ▸ Orders | list · kanban · pivot · graph · drill ✓ · XLSX ✓¹ · PDF per document only | Sales Orders, My Orders, To Invoice, To Upsell, Late Availability | AVAILABLE BUT NOT EXPOSED in the hub (reachable in its app menu) | as above | B | MUST | Screen + PDF register + XLSX | Sales user, Sales manager |
| 3 | Sales Order Lines | Operational | Yes | sale · sale.report (one row per order line) | Sales Analysis ▸ list view. NOTE: Community has NO `sale.order.line` action at all | graph · pivot · list · drill ✓ (cell → records) · XLSX ✓¹ · PDF ✗ | Quotations / Sales Orders / To Invoice / Fully Invoiced; group by customer, product, status, date | EXPOSED — Reports hub | Opens on the graph tab, so it reads as a chart; the list tab is the operational view | B — a hub card that opens the same action on the LIST view | MUST | Screen + XLSX | Sales user, Sales manager |
| 4 | Confirmed Sales | Operational | Yes | sale · sale.report | Sales Analysis, filter "Sales Orders" | graph · pivot · list · drill ✓ (cell → records) · XLSX ✓¹ · PDF ✗ | "Sales Orders" = state not in draft/cancel/sent | EXPOSED — Reports hub | — | A | MUST | Screen + XLSX | Sales user, manager |
| 5 | Sales by Customer | Analytical | Yes | sale · sale.report | Sales ▸ Reporting ▸ By Customers (`action_order_report_customers`) | graph · pivot · list · drill ✓ (cell → records) · XLSX ✓¹ · PDF ✗ | group by customer, country, industry | EXPOSED — Reports hub | — | A | MUST | Pivot/graph + XLSX | Sales user, manager |
| 6 | Sales by Product | Analytical | Yes | sale · sale.report | By Products (`action_order_report_products`) | graph · pivot · list · drill ✓ (cell → records) · XLSX ✓¹ · PDF ✗ | product, variant, category | EXPOSED — Reports hub | — | A | MUST | Pivot/graph + XLSX | Sales user, manager |
| 7 | Sales by Salesperson | Analytical | Yes | sale · sale.report | By Salespersons (`action_order_report_salesperson`) | graph · pivot · list · drill ✓ (cell → records) · XLSX ✓¹ · PDF ✗ | salesperson | EXPOSED — Reports hub | — | A | MUST | Pivot/graph + XLSX | Sales user (own rows), manager (all) |
| 8 | Sales by Sales Team | Analytical | Yes | sale · sale.report | `action_order_report_so_salesteam` / `_quotation_salesteam` (graph,list) — no menu; opened from the Sales Teams kanban | graph · pivot · list · drill ✓ (cell → records) · XLSX ✓¹ · PDF ✗ | group by Sales Team | AVAILABLE BUT NOT EXPOSED (native action exists, no menu) | The only team-scoped actions have no menu; the team is a plain group-by in the main analysis | A — group-by is enough; B only if a dedicated card is wanted | SHOULD | Pivot/graph + XLSX | Sales manager |
| 9 | Sales by Product Category | Analytical | Yes | sale · sale.report | Sales Analysis, group by "Product Category" | graph · pivot · list · drill ✓ (cell → records) · XLSX ✓¹ · PDF ✗ | categ_id | EXPOSED — Reports hub | — | A | SHOULD | Pivot + XLSX | Sales user, manager |
| 10 | Sales by Date | Analytical | Yes | sale · sale.report | Sales Analysis, group by Order Date (day / month / year) | graph · pivot · list · drill ✓ (cell → records) · XLSX ✓¹ · PDF ✗ | date, "Last 365 days" default | EXPOSED — Reports hub | — | A | MUST | Graph/pivot + XLSX | Sales user, manager |
| 11 | Customer Order History | Operational | Yes | sale · sale.order via res.partner | Customer form ▸ "Sales" smart button; Sales ▸ Orders ▸ Customers | list · form · drill ✓ · XLSX ✓¹ · PDF per document only | Customer filter on the orders list | AVAILABLE BUT NOT EXPOSED in the hub (reachable in its app menu) | Reachable per customer; no hub card | A — expose natively (customers menu already exists) | SHOULD | Screen + XLSX | Sales user, manager |
| 12 | Sales Returns / Credit Notes | Operational | Partly | account · account.invoice.report; stock · return pickings | Invoice Analysis, filter "Credit Notes"; return pickings + "Return slip" PDF | graph · pivot · list · drill ✓ (cell → records) · XLSX ✓¹ · PDF ✗ | "Credit Notes", Customers, Due Date, Status | PARTIAL | Credit notes are in Invoice Analysis (hub, accounting roles only); stock returns are separate transfers; no single "returns" view | F — combine the two native views; no new report | SHOULD | Screen + XLSX | Sales manager, Accountant |
| 13 | Delivered Sales | Analytical | Yes | sale · sale.report | Sales Analysis measures Qty Delivered / Qty To Deliver (needs sale_stock) | graph · pivot · list · drill ✓ (cell → records) · XLSX ✓¹ · PDF ✗ | measures + invoice status | EXPOSED — Reports hub | — | A | SHOULD | Pivot + XLSX | Sales manager |
| 14 | Invoiced Sales | Analytical | Yes | sale · sale.report; account · account.invoice.report | Sales Analysis (Qty Invoiced, Untaxed Amount Invoiced); Invoice Analysis | graph · pivot · list · drill ✓ (cell → records) · XLSX ✓¹ · PDF ✗ | "Fully Invoiced", "To Invoice"; Invoiced, Invoices | EXPOSED — Reports hub | Invoice Analysis only for accounting roles | A | MUST | Pivot + XLSX | Sales manager, Accountant |
| 15 | Sales Margin / Profitability | Analytical | Partly | account.invoice.report (`price_margin`, `inventory_value`); sale_margin (core, NOT installed) | Invoice Analysis measures; `sale.report` has NO margin field (verified) | graph · pivot · list · drill ✓ (cell → records) · XLSX ✓¹ · PDF ✗ | group by customer / product | IN COMMUNITY, MODULE NOT INSTALLED | `sale_margin` is an Odoo core module that is not installed (changing module state needs approval); invoice margin exists only when product cost is set | A — install `sale_margin` (approval) ; meanwhile B — Invoice Analysis profitability cards | SHOULD | Pivot + XLSX | Sales manager, CEO |
| 16 | Outstanding Sales Orders | Operational | Yes | sale · sale.order / sale.report | "To Invoice" and "To Upsell" menus; measures Qty To Deliver / To Invoice | list · kanban · pivot · graph · drill ✓ · XLSX ✓¹ · PDF per document only | To Invoice, To Upsell, Late Availability | AVAILABLE BUT NOT EXPOSED in the hub (reachable in its app menu) | Reachable in the Sales app only | B | MUST | Screen + PDF register + XLSX | Sales user, manager |
| 17 | Cancelled Sales | Operational | Yes | sale · sale.report / sale.order | Sales Analysis includes cancelled lines (no state filter in the view); group by Status | graph · pivot · list · drill ✓ (cell → records) · XLSX ✓¹ · PDF ✗ | Status group-by (the orders search has no "Cancelled" filter) | EXPOSED — Reports hub | — | A | SHOULD | Screen + XLSX | Sales manager |
| 18 | Top Customers | Management | Partly | ncollection_account_dashboard card; sale.report pivot | Finance dashboard "Top Customers"; pivot sorted by amount | dashboard card · pivot | — | EXPOSED — dashboard card | A card, not a report | A — keep card; pivot is the drill | SHOULD | Dashboard + pivot | CEO, Sales manager |
| 19 | Top Products | Analytical | Yes | sale · sale.report | Sales by Product, sorted by amount | graph · pivot · list · drill ✓ (cell → records) · XLSX ✓¹ · PDF ✗ | product | EXPOSED — Reports hub | — | A | SHOULD | Pivot/graph | Sales manager |
| 20 | Sales vs Target | Management | No | crm.team `invoiced_target` (kanban only); OCA `sales_team_invoiced_target_report` (unpinned repo) | Sales Teams kanban shows target vs invoiced; no report | — | — | NOT IN COMMUNITY | Not a Community report; the OCA module lives in a repo that is not pinned | O — adopt after pinning OCA/sale-reporting (approval) | ADVANCED | Pivot + XLSX | Sales manager, CEO |
| 21 | Salesperson Leaderboard | Management | Partly | ncollection_account_dashboard card | Sales department dashboard | dashboard card | — | EXPOSED — dashboard card | — | A | SHOULD | Dashboard | Sales manager |
| 22 | Sales order status analysis | Analytical | Yes | sale · sale.report | Sales Analysis, group by Status / Order Invoice Status | graph · pivot · list · drill ✓ (cell → records) · XLSX ✓¹ · PDF ✗ | Status, invoice status | EXPOSED — Reports hub | — | A | SHOULD | Pivot + XLSX | Sales manager |

#### 6.2 PURCHASES (20)

| # | Item | Class | In Community? | Module · model | Native screen | Capabilities | Filters / grouping (read from the views) | NCollection today | Why not exposed | Decision | Priority | Target output | Roles |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Purchase Orders | Operational | Yes | purchase · purchase.order | Purchase ▸ Orders ▸ Purchase Orders | list · kanban · pivot · graph · drill ✓ · XLSX ✓¹ · PDF per document only | Purchase Orders, Late, Late Receipts, Waiting Bills, To Approve, Buyer, Vendor | AVAILABLE BUT NOT EXPOSED in the hub (reachable in its app menu) | Reachable only in the Purchase app, which no NCollection role can open (no Purchase role) — D-1 | B — hub card onto the same action, once a role can see it | MUST | Screen + PDF register + XLSX; PDF per PO (native "Purchase Order") | Purchase user, Purchase manager |
| 2 | Purchase Order Lines | Operational | Yes | purchase · purchase.order.line | `purchase.action_purchase_history` (list, pivot, graph) — NO menu; only a product smart button | graph · pivot · list · drill ✓ (cell → records) · XLSX ✓¹ · PDF ✗ | product history | AVAILABLE BUT NOT EXPOSED (native action exists, no menu) | The action exists without a menu | B — a hub menu onto the existing action | MUST | Screen + XLSX | Purchase user, manager |
| 3 | RFQs | Operational | Yes | purchase · purchase.order | Purchase ▸ Orders ▸ Requests for Quotation | list · kanban · pivot · graph · drill ✓ · XLSX ✓¹ · PDF per document only | New, Sent, To Approve, Buyer, Vendor | AVAILABLE BUT NOT EXPOSED in the hub (reachable in its app menu) | as Purchase Orders | B | MUST | Screen + XLSX; PDF per RFQ (native "Request for Quotation") | Purchase user, manager |
| 4 | Confirmed Purchase Orders | Operational | Yes | purchase · purchase.order / purchase.report | Purchase Orders menu (domain state = purchase); Purchase Analysis filter | list · kanban · pivot · graph · drill ✓ · XLSX ✓¹ · PDF per document only | Purchase Orders | AVAILABLE BUT NOT EXPOSED in the hub (reachable in its app menu) | as above | B | MUST | Screen + PDF register + XLSX | Purchase user, manager |
| 5 | Purchases by Vendor | Analytical | Yes | purchase · purchase.report | Hub menu over the base purchase analysis action (`menu_purchase_by_vendor`) | graph · pivot · list · drill ✓ (cell → records) · XLSX ✓¹ · PDF ✗ | Vendor, Vendor Country | EXPOSED — Reports hub | Visible only to a Purchase User | A | MUST | Pivot/graph + XLSX | Purchase user, manager |
| 6 | Purchases by Product | Analytical | Yes | purchase · purchase.report | Hub menu `menu_purchase_by_product` | graph · pivot · list · drill ✓ (cell → records) · XLSX ✓¹ · PDF ✗ | Product, Product Category | EXPOSED — Reports hub | as above | A | MUST | Pivot/graph + XLSX | Purchase user, manager |
| 7 | Purchases by Buyer | Analytical | Yes | purchase · purchase.report | Purchase Analysis, group by Buyer | graph · pivot · list · drill ✓ (cell → records) · XLSX ✓¹ · PDF ✗ | Buyer | EXPOSED — Reports hub | — | A | SHOULD | Pivot + XLSX | Purchase manager |
| 8 | Purchases by Date | Analytical | Yes | purchase · purchase.report | Purchase Analysis, Order / Confirmation Date | graph · pivot · list · drill ✓ (cell → records) · XLSX ✓¹ · PDF ✗ | Order Date, Confirmation Date | EXPOSED — Reports hub | — | A | MUST | Graph/pivot + XLSX | Purchase user, manager |
| 9 | Vendor Cost Analysis | Analytical | Partly | purchase.report (`price_average` "Average Cost"); stock_account.stock_avco_report "Unit Cost History" | Purchase Analysis measure; Unit Cost History action has NO menu | graph · pivot · list · drill ✓ (cell → records) · XLSX ✓¹ · PDF ✗ | Product, Vendor | PARTIAL | Average cost is native; the cost-history list is unmenued | A + B — measure now; hub menu for cost history | SHOULD | Pivot + XLSX | Purchase manager, Accountant |
| 10 | Vendor Order History | Operational | Yes | purchase · purchase.order via res.partner | Vendor form ▸ "Purchase" smart button; Purchase ▸ Orders ▸ Vendors | list · form · drill ✓ · XLSX ✓¹ · PDF per document only | Vendor filter | AVAILABLE BUT NOT EXPOSED in the hub (reachable in its app menu) | as Purchase Orders | A | SHOULD | Screen + XLSX | Purchase user, manager |
| 11 | Vendor Performance | Analytical | Yes | purchase_stock · vendor.delay.report | "On-time Delivery" action — had NO menu; hub entry added 2026-10-09 | graph only · XLSX ✗ (graph) · PDF ✗ | Vendor, Product, Category, Date | EXPOSED — Reports hub | Native view is a chart; the tabular form is purchase.report "Days to Receive" | B + A — keep the chart, add a pivot card on purchase.report | SHOULD | Graph + pivot + XLSX | Purchase manager |
| 12 | Days to Confirm | Analytical | Yes | purchase · purchase.report (`delay`) | Purchase Analysis measure "Days to Confirm" | graph · pivot · list · drill ✓ (cell → records) · XLSX ✓¹ · PDF ✗ | Confirmation Date | EXPOSED — Reports hub | A measure, not a report | A | SHOULD | Pivot + XLSX | Purchase manager |
| 13 | Days to Receive | Analytical | Yes | purchase_stock · purchase.report (`delay_pass`, `days_to_arrival`) | Purchase Analysis measures "Days to Receive", "Effective Days To Arrival" | graph · pivot · list · drill ✓ (cell → records) · XLSX ✓¹ · PDF ✗ | Order Date | EXPOSED — Reports hub | as above | A | SHOULD | Pivot + XLSX | Purchase manager |
| 14 | Received vs Ordered | Analytical | Yes | purchase · purchase.report | Measures Qty Ordered / Received / Billed / To be Billed | graph · pivot · list · drill ✓ (cell → records) · XLSX ✓¹ · PDF ✗ | Status, Vendor | EXPOSED — Reports hub | — | A | MUST | Pivot + XLSX | Purchase user, manager |
| 15 | Purchase Returns | Operational | Partly | stock · return pickings; account · vendor refunds | Return transfers; Vendor Bill Analysis on credit notes | graph · pivot · list · drill ✓ (cell → records) · XLSX ✓¹ · PDF ✗ | — | PARTIAL | Two native views, no single returns view | F — combine; no new report | SHOULD | Screen + XLSX | Purchase manager |
| 16 | Vendor Bills | Operational | Yes | account · account.invoice.report | `action_account_invoice_report_all_supp` "Bills Analysis" — hub entry added 2026-10-09 | graph · pivot · list · drill ✓ (cell → records) · XLSX ✓¹ · PDF ✗ | Vendors, Invoiced, Due Date, Status | EXPOSED — Reports hub | Accounting roles only | A | MUST | Pivot + XLSX | Purchase manager, Accountant |
| 17 | Outstanding Purchase Orders | Operational | Yes | purchase · purchase.order | Purchase Orders list with filters "Late Receipts", "Waiting Bills" | list · kanban · pivot · graph · drill ✓ · XLSX ✓¹ · PDF per document only | Late, Late Receipts, Waiting Bills | AVAILABLE BUT NOT EXPOSED in the hub (reachable in its app menu) | as Purchase Orders | B | MUST | Screen + PDF register + XLSX | Purchase user, manager |
| 18 | Cancelled Purchases | Operational | Yes | purchase · purchase.report | Purchase Analysis includes cancelled lines (no state filter); group by Status | graph · pivot · list · drill ✓ (cell → records) · XLSX ✓¹ · PDF ✗ | Status | EXPOSED — Reports hub | — | A | SHOULD | Screen + XLSX | Purchase manager |
| 19 | Purchase Price Variance | Analytical | No | — (needs PO price vs standard cost) | No Community report; inputs exist (`price_average`, product `standard_price`) | — | — | NOT IN COMMUNITY | Not provided by Community or the pinned OCA repos | E — small custom report on the existing engine | ADVANCED | Screen + XLSX | Purchase manager, Accountant |
| 20 | Vendor Payment History | Operational | Yes | account · account.payment | "Vendor Payments" — hub entry added 2026-10-09 | list · form · drill ✓ · XLSX ✓¹ · PDF per document only | partner, journal, date | EXPOSED — Reports hub | Accounting roles only | A | SHOULD | Screen + XLSX | Accountant, Purchase manager |

#### 6.3 INVENTORY (22)

| # | Item | Class | In Community? | Module · model | Native screen | Capabilities | Filters / grouping (read from the views) | NCollection today | Why not exposed | Decision | Priority | Target output | Roles |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Stock Report / Stock On Hand | Operational | Yes | stock · product.product | Stock Analysis (list: On Hand, Free To Use, Forecasted, Incoming, Outgoing, Cost, Total Value) | list · form · drill ✓ · XLSX ✓¹ · PDF ✗ (only the physical-inventory "Count Sheet") | Available Products, Negative Forecasted Quantity, To Order, Category | EXPOSED — Reports hub | Native screen is a plain list; there is no printable stock report in Community | A for screen; C/E-light — printable "Stock on Hand" on the report engine | MUST | Screen + PDF + XLSX | Inventory user, Inventory manager |
| 2 | Stock by Product | Operational | Yes | stock · product.product / stock.quant | Stock Analysis | as above | Product | EXPOSED — Reports hub | — | A | MUST | Screen + XLSX | Inventory user |
| 3 | Stock by Warehouse | Analytical | Yes | stock · stock.quant (`warehouse_id`) | `stock.stock_quant_action` "Locations" (list, form; the model also has pivot + graph views) — see Stock by Location | list · pivot · graph · drill ✓ · XLSX ✓¹ · PDF ✗ | Internal Locations, Transit, Negative Stock, Product Category | AVAILABLE BUT NOT EXPOSED (native action exists, no menu) | The quant action has no menu of its own; the one menu to it is gated on multi-location | B — hub card onto the quant action grouped by warehouse | MUST | Pivot + XLSX | Inventory user, manager |
| 4 | Stock by Location | Operational | Yes | stock · stock.quant | Inventory ▸ Reporting ▸ "Inventory Valuation" server action → `action_view_quants` (menu groups: Manage Multiple Stock Locations / Technical Features / Owners) | list · pivot · graph · drill ✓ · XLSX ✓¹ · PDF ✗ | Location, Lot/Serial, Package, Owner | AVAILABLE BUT NOT EXPOSED (native action exists, no menu) | Menu is gated on the multi-location setting (off), so the catalog skipped it | B — expose when multi-location is enabled | SHOULD | Screen + XLSX | Inventory user, manager |
| 5 | Stock by Category | Analytical | Yes | stock · product.product / stock.quant | Group by Product Category | as above | Product Category | EXPOSED — Reports hub | — | A | SHOULD | Pivot + XLSX | Inventory user |
| 6 | Reserved Stock | Operational | Yes | stock · stock.quant (`reserved_quantity`, `available_quantity`); product `free_qty` | Locations list columns; Stock Analysis "Free To Use" | list · pivot | — | PARTIAL | On the product list (hub); per-location reservation only in the unmenued quant list | B (with Stock by Location) | SHOULD | Screen + XLSX | Inventory user |
| 7 | Incoming Stock | Operational | Yes | stock · product.incoming_qty; stock.move | Stock Analysis "Incoming"; Moves Analysis filter "Incoming"; Receipts list | list · pivot · graph | Incoming, Ready, Remaining | EXPOSED — Reports hub | — | A | MUST | Screen + XLSX | Inventory user |
| 8 | Outgoing Stock | Operational | Yes | stock · product.outgoing_qty; stock.move | Stock Analysis "Outgoing"; Moves Analysis filter "Outgoing"; Deliveries list | list · pivot · graph | Outgoing | EXPOSED — Reports hub | — | A | MUST | Screen + XLSX | Inventory user |
| 9 | Inventory Moves History | Operational | Yes | stock · stock.move.line | Moves History (`stock.stock_move_line_action`) | list · kanban · pivot · drill ✓ · XLSX ✓¹ · PDF ✗ | Done, Incoming, Internal, Outgoing, Inventory Adjustments, Transfers, Last 30 days / 3 / 12 months | EXPOSED — Reports hub | — | A | MUST | Screen + XLSX | Inventory user |
| 10 | Inventory Moves Analysis | Analytical | Yes | stock · stock.move | Moves Analysis (`stock.stock_move_action`) | pivot · list · graph · kanban · drill ✓ · XLSX ✓¹ | Done, Incoming, Outgoing, Inventory, Ready, Remaining | EXPOSED — Reports hub | — | A | MUST | Pivot + XLSX | Inventory user |
| 11 | Receipts | Operational | Yes | stock · stock.picking | Inventory ▸ Operations ▸ Transfers ▸ Receipts (server action) | list · kanban · calendar · drill ✓ · XLSX ✓¹ · PDF per document ("Delivery Slip", "Picking Operations", "Reception Report") | Ready, Waiting, Late, Backorders | AVAILABLE BUT NOT EXPOSED in the hub (reachable in its app menu) | Operations menu in the Inventory app; hub lists reports only | B | MUST | Screen + XLSX; native PDF per transfer | Inventory user |
| 12 | Deliveries | Operational | Yes | stock · stock.picking | … ▸ Deliveries (server action) | as Receipts | as Receipts | AVAILABLE BUT NOT EXPOSED in the hub (reachable in its app menu) | as Receipts | B | MUST | Screen + XLSX; native PDF per transfer | Inventory user |
| 13 | Internal Transfers | Operational | Yes | stock · stock.picking | … ▸ Internal (menu gated on multi-location) | as Receipts | as Receipts | AVAILABLE BUT NOT EXPOSED in the hub (reachable in its app menu) | gated on the multi-location setting | B | SHOULD | Screen + XLSX | Inventory user |
| 14 | Scrap Report | Analytical | Yes | stock · stock.scrap | Inventory ▸ Operations ▸ Adjustments ▸ Scrap; hub entry added 2026-10-09 | list · pivot · graph · kanban | product, reason, date | EXPOSED — Reports hub | — | A | SHOULD | Screen + XLSX | Inventory user |
| 15 | Inventory Adjustments | Operational | Yes | stock · stock.quant (Physical Inventory); stock.move.line | "Physical Inventory" server action; Moves History filter "Inventory Adjustments"; PDF "Count Sheet" | list · XLSX ✓¹ · PDF ✓ (Count Sheet) | To Count, To Apply, My Counts, Conflicts | AVAILABLE BUT NOT EXPOSED in the hub (reachable in its app menu) | Operations menu; the history is the Moves History filter | B | SHOULD | Screen + PDF (Count Sheet) + XLSX | Inventory user, manager |
| 16 | Inventory Valuation | Operational | Partly | stock_account · product.total_value; stock.quant `value`; `stock_move_valuation_action` (per-move, no menu) | Stock Analysis "Total Value"; the per-move valuation list has NO menu | list · XLSX ✓¹ · PDF ✗ | Category, Product | PARTIAL | Value is on the product list; per-move valuation unmenued; menu "Inventory Valuation" is only the multi-location quant view | B — expose the per-move valuation list; C/E-light — printable valuation | MUST | Screen + PDF + XLSX | Inventory manager, Accountant |
| 17 | Forecasted Inventory | Analytical | Partly | stock · product.virtual_available; forecast report | Stock Analysis "Forecasted"; the forecast report is a button on the product form; Replenishment is gated on "Inventory / Administrator" | list · client report | Negative Forecasted Quantity, To Order | PARTIAL | Replenishment menu needs an Inventory Administrator, which no NCollection role is | B (needs an Inventory-manager role — D-1b) | SHOULD | Screen + XLSX | Inventory manager |
| 18 | Stock Aging | Analytical | No | stock.quant `in_date` (data exists) | No Community report; no OCA module in the pinned or checked repos | — | — | NOT IN COMMUNITY | Not provided | E — NCollection report on the engine (new module, needs stock) | SHOULD | Screen + PDF + XLSX | Inventory manager |
| 19 | Slow-Moving Inventory | Analytical | No | derivable from last done stock.move.line per product | No Community report | — | — | NOT IN COMMUNITY | Not provided | E — second view of the aging report | ADVANCED | Screen + XLSX | Inventory manager |
| 20 | Inventory Turnover | Management | Partly | ncollection_core KPI `inventory_turnover` (Warehouse dashboard) | KPI card only | dashboard card | — | EXPOSED — dashboard card | A KPI, not a report | E — report view over the existing KPI | ADVANCED | Dashboard + screen | Inventory manager, CEO |
| 21 | Dead Stock | Analytical | No | — (stock with no movement for N days) | No Community report | — | — | NOT IN COMMUNITY | Not provided | E — variant of Slow-Moving | ADVANCED | Screen + XLSX | Inventory manager |
| 22 | Reordering / Replenishment Analysis | Operational | Yes | stock · stock.warehouse.orderpoint | Inventory ▸ Operations ▸ Procurement ▸ Replenishment (Inventory / Administrator); Reordering Rules action has no menu | list · kanban · form | To Order | AVAILABLE BUT NOT EXPOSED in the hub (reachable in its app menu) | Gated on the Inventory Administrator group | B (needs Inventory-manager role — D-1b) | SHOULD | Screen + XLSX | Inventory manager |

### 7. Printable output — the right output for the right report (section 39)

The target is not "everything is PDF". Rule: **PDF only where a person prints or sends it; XLSX where the data is worked
further; screen for exploration; dashboard for a KPI.**

| Output | Reports | Native today? |
|---|---|---|
| **Screen + PDF + XLSX** | Sales Order Register, Purchase Order Register, Stock on Hand, Inventory Valuation, Stock Aging, Outstanding Orders (sales and purchase) | **No** — PDF is per document only; this is the real Community gap (§9, E-light) |
| **Per-document PDF (native)** | Quotation / Order, Purchase Order, RFQ, Delivery Slip, Picking Operations, Reception Report, Return slip, Count Sheet | **Yes** — keep, expose from the document |
| **Screen / pivot + XLSX** | Sales trend, by customer / product / salesperson / team / category / date; purchases by vendor / product / buyer; Moves Analysis; Moves History; Received vs Ordered; Days to Confirm / Receive | **Yes** |
| **Screen + XLSX** | Receipts, Deliveries, Internal, Scrap, Customer / Vendor Order History, Purchase Order Lines | **Yes** |
| **Dashboard / chart** | Top Customers, Salesperson Leaderboard, Inventory Turnover, Vendor On-time Delivery chart | **Yes** (NCollection dashboards, native graph) |

### 8. Role-based catalog (section 38)

NCollection has **8 roles** (Employee, Sales, Warehouse, HR, Accountant, Manager, CEO, Owner). The personas you listed map
as follows — and **three of the six have no role**:

| Persona | NCollection role today | Should see (Operational · Analysis · Management) | Reaches it today |
|---|---|---|---|
| **Sales user** | Sales (own documents) | Quotations, Orders, Order Lines, Orders to Invoice, Customer history · Sales analysis (own rows) | Operational ✓ in the Sales app; analysis ✓ (since 2026-10-09); **no hub cards for operational lists** |
| **Sales manager** | Manager (all documents), CEO, Owner | all of the above + team / salesperson analysis, Sales vs Target, margin (authorised) | same; margin and target not available |
| **Purchase user** | **none** (Odoo group only) | RFQs, POs, PO lines, vendors, purchase analysis | **Only a user given the Odoo Purchase group** |
| **Purchase manager** | **none** | + vendor performance, cost analysis, price variance, trends | same — **Owner / CEO / Manager cannot see Purchase at all (D-1)** |
| **Inventory user** | Warehouse | Receipts, Deliveries, Moves History, Stock on Hand, Scrap | Operations ✓ in the Inventory app; analysis ✓; no hub cards for operations |
| **Inventory manager** | **none** (needs `Inventory / Administrator`) | + valuation, aging, turnover, warehouse / location analysis, replenishment | **Unreachable (D-1b)** — valuation-style screens and Replenishment are Administrator-gated |

Security rule that must not be broken: **a hub card is shown only if the user can read the model** (already enforced), and a
menu is aligned to the **least group that already holds the ORM read right** — exposure never widens data access. ORM
notes carried over from the audit: the Sales analysis is row-scoped by Odoo record rules (own vs all documents); stock
moves and quants are readable by every internal user at the ORM layer (Odoo default, D-3).

### 9. What to expose, what to adapt, what to build

**1 — Simply expose (B/A): 18 items are AVAILABLE BUT NOT EXPOSED and need only a hub card or menu onto an existing native action — no new report.**
Quotations · Sales Orders · Sales Order Lines (list-first) · Orders to Invoice / outstanding · Customer history · Purchase Orders ·
RFQs · Purchase Order Lines · Vendor history · Outstanding POs · Receipts · Deliveries · Internal · Physical Inventory ·
Stock by Warehouse / Location (quant action) · Inventory Valuation (per-move list) · Unit Cost History · Replenishment.

**2 — Wrap / adapter (C/D): none required for the screens above.** The native lists, pivots and graphs are good and already
localise and mirror to RTL. A wrapper (the NCollection viewer, engine PDF/XLSX) is justified **only** for the printable
registers in §7.

**3 — OCA (O):** `sales_team_invoiced_target_report` for Sales vs Target — **after** the repo is pinned (approval). Nothing in the
pinned repos covers aging, slow-moving or price variance (checked: stock-logistics-reporting offers value-per-move and demand
info, not aging).

**4 — Genuinely custom (E), and only these:**

| Report | Why native cannot do it | Size |
|---|---|---|
| **Printable registers** — Sales Order, Purchase Order, Stock on Hand, Valuation — on the existing report engine (`ncollection.account.report` pattern, one **generic document-list engine**, not one report per screen) | Community has no aggregate PDF; the lists have no printable form | M for the engine + S per register |
| **Stock Aging / Slow-Moving / Dead Stock** | no Community or pinned-OCA report; `stock.quant.in_date` exists | M + S |
| **Purchase Price Variance** | no report; inputs exist | S–M |
| **Inventory Turnover** report view | KPI exists, no report | M |

Everything else is exposure, not development.

### 10. Final target structure

Based on repository evidence (the hub supports only a flat card list per category today, so sub-groups need a small hub
change — design only, not done):

```
REPORTS
├── Financial & Accounting      Statements · Ledgers · Financial Statements · Tax          (exists, complete)
├── Sales
│   ├── Operational Reports     Quotations · Sales Orders · Order Lines · Orders to Invoice · Customer history · Returns
│   ├── Sales Analysis          by customer / product / salesperson / team / category / date · delivered · invoiced · status
│   └── Management Reports      Margin · Top customers / products · Sales vs Target · Leaderboard
├── Purchases
│   ├── Operational Reports     RFQs · Purchase Orders · PO Lines · Outstanding POs · Vendor history · Vendor bills
│   ├── Purchase Analysis       by vendor / product / buyer / date · Received vs Ordered · Days to Confirm / Receive · Cost
│   └── Vendor Reports          Vendor Performance (on-time) · Price Variance · Vendor Payments
├── Inventory
│   ├── Stock Reports           Stock on Hand · by Warehouse / Location / Category · Reserved · Valuation · Aging
│   ├── Movement Reports        Moves History · Receipts · Deliveries · Internal · Scrap · Adjustments
│   ├── Warehouse Reports       Replenishment · Forecast · Slow-moving · Dead stock
│   └── Inventory Analysis      Moves Analysis · Turnover
└── Management & Analytics      KPIs · Dashboards · Advanced analytics
```

### 11. Priorities and next steps (no code until approved)

| Priority | Count | Meaning |
|---|---|---|
| MUST | 30 | the operational core — almost all are **expose-only** |
| SHOULD | 29 | analysis depth, returns, history |
| ADVANCED | 5 | price variance, dead stock, sales vs target, turnover |

1. **Approve exposure (B):** a hub card per native operational screen (18 items, no new report code) — the single change that
   answers your concern. Includes the three unmenued actions and list-first cards.
2. **Decide the roles (D-1, D-1b):** a Purchase user / manager and an Inventory manager, because three of six personas cannot
   reach their reports otherwise.
3. **Approve one generic printable-register engine** for §7 (the real Community gap), then registers one at a time.
4. **Approve (separately):** `sale_margin` (core module state), OCA/sale-reporting pin (Sales vs Target), a stock-aging
   module that depends on `stock`.

Decision counts across the 64 items: A 36 · B 20 · C 0 · D 0 · E 5 · F 2 · O 1 (a leading letter; some rows combine two).
Classes: Operational 31 · Analytical 29 · Management 4 · Chart 0 (graph-only cards are
tagged by their data class; "Vendor Performance" is chart-form).
