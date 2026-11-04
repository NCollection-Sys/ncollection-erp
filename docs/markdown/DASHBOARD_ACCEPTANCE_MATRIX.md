# Dashboard inventory and acceptance matrix (DASH-02)

Every dashboard the repository implements, every KPI on it, and the evidence for each. Statuses:
**VERIFIED** (a deterministic test or a ledger cross-check shows the figure is right), **CORRECTED AND VERIFIED**
(a defect was reproduced by a failing test first, then fixed), **BLOCKED — SPECIFIC DATA DEPENDENCY**,
**NOT IMPLEMENTED — EXPLICITLY OUT OF SCOPE**. Nothing is "assumed right because the query runs".

## 1. Inventory

| # | Dashboard | Route (`/atlas/…`) | Provider | Role | Scope |
|---|-----------|--------------------|----------|------|-------|
| 1 | **Dashboards** (Odoo Spreadsheet Dashboards: Sales, Product, Invoicing, Warehouse Metrics, Point of Sale, Expenses, Live Chat) | `dashboards` | Odoo `spreadsheet_dashboard*` (formulas evaluated in the browser as the user) | every internal user the dashboard's own groups admit | session company; Odoo's record rules |
| 2 | **Workspace dashboard** (Summary) | `action-ncollection_core.action_ncollection_dashboard` | `ncollection.dashboard.data.get_dashboard_payload` (`ncollection_core`) | by widget group (personal / pipeline / operations / financial) | the **active company** |
| 3 | Finance | `action-ncollection_account_dashboard.action_finance_dashboard` | `ncollection.account.dashboard.service.get_finance_dashboard` over `ncollection.account.report.summary` | Accountant, CEO, Owner | active company, YTD |
| 4 | Accountant | `…action_accountant_dashboard` | same service over `ncollection.account.report.profitability` | same | active company, YTD |
| 5 | Cash | `…action_cash_dashboard` | same service over the summary report | same | active company |
| 6 | CEO | `…action_ceo_dashboard` | same service + the aggregation engine (pipeline, top customers); optional period | CEO, Owner | active company |
| 7 | Sales | `…action_sales_dashboard` | `ncollection.kpi` + engine | Sales role (not Owner / CEO / Manager) | ORM rules |
| 8 | HR | `…action_hr_dashboard` | `ncollection.kpi` + engine | HR role | ORM rules |
| 9 | Warehouse | `…action_warehouse_dashboard` | `ncollection.kpi` + engine + `stock_valuation_ranking` | Warehouse role | ORM rules; cost data needs Inventory Manager |
| 10 | Platform Summary (business management) | platform menu `Summary` | `ncollection.subscription.dashboard` (`ncollection_subscription`, extended by `ncollection_saas`) | Platform Administrator only (group + ORM) | the platform database |

Not dashboards (searched, not invented): the Reseller screens are scoped lists and forms (no KPI cards); the Anomaly
Alerts screen is a list; `ncollection_account_analytics` KPIs are reports. There is no `/atlas/dashboards`
implementation of our own: that route is Odoo's Spreadsheet Dashboards.

Common behaviour: refresh = reloaded on open and on the period / company change (no client cache; the aggregation engine's
server cache is keyed by user, language, time zone, allowed companies and the dataset version); export = Workspace
(Excel / PDF via `/ncollection/dashboard/export/<fmt>`) and every real (non-sample) Spreadsheet Dashboard
(`/ncollection/dashboards/export/<fmt>`); the Finance / Accountant / Cash / CEO / department dashboards have no export
and are covered by the Reports hub exports of the same figures.

## 2. Currency, company and date policy (what every figure obeys)

* **Company.** A dashboard speaks for the **active company**. A user with two companies ticked never sees the two ledgers
  added together (each in its own currency): the Workspace payload is scoped to the active company
  (`test_d_the_dashboard_speaks_for_the_active_company_only`); the report-backed dashboards already carry one `company_id`
  (`test_o_one_company_never_adds_in_another_companys_money`).
* **Currency.** Ledger figures are in the company currency by construction (`balance`, `amount_residual`). Order totals
  (sales / purchases / rankings / average deal) are in the order's currency and are converted to the company currency
  **at the order date** (`res.currency._convert`), never added raw. Plans on the platform are converted at today's rate.
* **Dates.** "Today" is the **user's** date (`fields.Date.context_today`), never the server's. A timestamp bound is half-open
  (`>= from`, `< to + 1 day`). No dashboard provider reads `date.today()` or `datetime.now()` (grep-verified).
* **Posted only.** Ledger KPIs read posted lines; drafts and cancelled documents never count.

## 3. Acceptance matrix

Tests live in `custom_addons/<module>/tests/`. `KA` = `ncollection_account_dashboard/tests/test_dashboard_kpi_acceptance.py`,
`WA` = `…/test_workspace_dashboard_accuracy.py`, `PA` = `ncollection_subscription/tests/test_dashboard_accuracy.py`.

### 3.1 Workspace dashboard

| Dashboard | KPI | Data source | Business definition | Expected | Actual | Test evidence | Status |
|---|---|---|---|---|---|---|---|
| Workspace | Sales this month | `sale.order` state `sale`, `date_order` in the user's month | Confirmed order value (tax-inclusive) in the company currency | 100 + (200 EUR = 100) = 200 | 200 (was 300) | `WA.test_b_sales_this_month_adds_one_currency`; `WA.test_c_this_month_is_the_users_month` | CORRECTED AND VERIFIED |
| Workspace | Receivables | `account.move.line` receivable, residual > 0 | What customers owe: open invoices | 100 with a 40 advance on file | 100 (was 60); ledger SQL 1,353.50 = dashboard 1,353.5 | `WA.test_a_receivables_…`; browser cross-check | CORRECTED AND VERIFIED |
| Workspace | Payables | same, payable, residual < 0 | What we owe suppliers | 100 with a 30 prepayment | 100 | `WA.test_a_payables_…` | CORRECTED AND VERIFIED |
| Workspace | Customer credit (new) | receivable lines, residual < 0 | Advances / credit notes not yet applied | 40 | 40; SQL 280 = dashboard 280 | `WA.test_a_receivables_…`; browser cross-check | CORRECTED AND VERIFIED |
| Workspace | Cash & bank | `asset_cash` posted balance | Money held; a payment in transit is not cash yet | +0 for a payment in transit, +40 for a bank entry | +0, +40 | `KA.test_ws_cash_is_the_liquidity_ledger_…` | VERIFIED |
| Workspace | Open quotations | `sale.order` draft + sent | Awaiting confirmation | +2 for one draft and one sent (not a confirmed, not a cancelled) | +2 | `KA.test_ws_open_quotations_count_draft_and_sent_only` | VERIFIED |
| Workspace | Purchases this month | `purchase.order` purchase/done, `date_approve` | Confirmed purchase value, company currency | 100 + (200 EUR = 100) = 200 | 200 (was 300) | `WA.test_b_purchases_this_month_adds_one_currency` | CORRECTED AND VERIFIED |
| Workspace | Stock on hand | `stock.quant` internal locations | Units in own warehouses (product unit) | 24 received − 1 dozen shipped = 12 | 12 | `KA.test_ws_stock_on_hand_counts_units_in_internal_locations` | VERIFIED |
| Workspace | Pending deliveries | `stock.picking` assigned / confirmed / waiting | Transfers waiting | +1 per confirmed goods order; 0 after cancel | as expected | `KA.test_ws_pending_deliveries_…` | VERIFIED |
| Workspace | Open activities | `mail.activity` of the user | Assigned to the user and not done | 1 of 2 (one belongs to another user); 0 after done | as expected | `KA.test_ws_open_activities_…` | VERIFIED |
| Workspace | Pending approvals | `approval.request` | — | n/a | widget omitted | the model belongs to Odoo Enterprise `approvals`, absent from Community; the widget is omitted by design | NOT IMPLEMENTED — EXPLICITLY OUT OF SCOPE |
| Workspace | Revenue, last 6 months (chart) | `income` lines grouped by month, posted | Revenue net of credit notes per month | +70 for 100 invoice − 30 credit note, draft 999 ignored | +70 | `KA.test_ws_revenue_trend_…` | VERIFIED |
| Workspace | Top customers (chart) | `sale.order` confirmed, period | Order value per customer, one currency | 200 for 100 + (200 EUR) | 200 | `WA.test_b_top_customers_adds_one_currency` | CORRECTED AND VERIFIED |
| Workspace | Expenses, Net profit | profitability report | Posted P&L of the period | = P&L | = P&L; net profit 2,690 = SQL revenue | `ncollection_account_reports/tests/test_summary*.py`; browser | VERIFIED |
| Workspace | Balance-sheet and P&L summary | report engine rows | The engine's own rows | = full report | = full report | `test_summary.py`, `test_summary_period.py` | VERIFIED |
| Workspace | Company scope | engine, active company | No sum of two ledgers | other company's 700 not added | 0 added; 700 with the other company active | `WA.test_d_…` | CORRECTED AND VERIFIED |
| Workspace | Export (Excel / PDF) | the payload above | The same figures | file = screen | verified | `ncollection_core/tests/test_dashboard_export.py` (14); browser | VERIFIED |

### 3.2 Finance, Accountant, Cash, CEO

| Dashboard | KPI | Data source | Business definition | Expected | Actual | Test evidence | Status |
|---|---|---|---|---|---|---|---|
| Finance / Cash | Receivables | `ncollection.account.open.items` (open receivable lines as of the reporting date) | Open invoices | 100 beside a 40 advance | 100 (was 60) | `KA.test_f_receivables_owed_and_customer_credit_are_shown_separately` | CORRECTED AND VERIFIED |
| Finance / Cash | Customer credit (new) | same, credit side | Unapplied payments and credit notes | 40 | 40 | same | CORRECTED AND VERIFIED |
| Finance / Cash | Payables | same, payable side | Open bills | 100 beside a 30 prepayment | 100 | `KA.test_f_payables_owed_and_vendor_prepayments_are_shown_separately` | CORRECTED AND VERIFIED |
| Finance / Cash | Vendor prepayments (new) | payable lines, debit residual **that originates from a payment** | Paid ahead to a supplier | 30; a 20 vendor credit note is **not** a prepayment | 30; 0 | same; `KA.test_f_a_vendor_credit_note_is_not_a_prepayment` | CORRECTED AND VERIFIED |
| Finance / Cash | Position as of the reporting date | partial reconciliations by `max_date` | Open on `date_to`, not today | not yet invoiced 0; unpaid on day −20: 100; paid since: 0 | as expected | `KA.test_f_the_position_is_as_of_the_reporting_date_not_today` | VERIFIED |
| Finance (report) | Payables sign | Financial Summary | A liability presents positive | +35 | **−35 before** (sign applied twice) → +35 | `KA.test_f_owed_minus_credit_is_the_ledger_balance` | CORRECTED AND VERIFIED |
| Finance / Cash | Owed − credit = ledger | Balance Sheet | The two sides reconcile to the ledger | equal | equal (1,353.5 − 280 = 1,073.5) | same; SQL | VERIFIED |
| Finance / Accountant / CEO | Revenue, Expenses, Net profit, margins | profitability + summary reports | Posted only, credit notes reduce revenue, drafts never count | +70 income, +50 net, +20 expense | as expected | `KA.test_p_revenue_expenses_and_net_profit_follow_posted_documents_only` | VERIFIED |
| Finance / Cash / CEO | Cash | `asset_cash` | Liquidity ledger (a customer payment in transit is not cash) | +0, then +40 | as expected | `KA.test_c_cash_is_the_liquidity_ledger_…` | VERIFIED |
| Finance / Cash / CEO | Assets, Liabilities | Balance Sheet buckets | The statement's buckets | = Balance Sheet | = Balance Sheet (2,823.5 / 133.5 = SQL 133.50) | `test_executive_reports.py`; browser | VERIFIED |
| Finance / Cash / CEO | Revenue trend (6 months) | summary report per month | Calendar months in the **user's** calendar | last label = user's current month | `Apr 2031` for Auckland at 31 Mar 20:00 UTC | `KA.test_t_the_trend_follows_the_users_calendar_not_the_servers` | VERIFIED |
| Finance / Cash | Company scope | report `company_id` | One company, never two ledgers added | other company's 300 not added | 0 | `KA.test_o_one_company_never_adds_in_another_companys_money` | VERIFIED |
| CEO | Pipeline funnel | `crm.lead` open opportunities | Open value per stage | engine rows | engine rows | `test_dashboard_service.py` | VERIFIED |
| CEO | Top customers | `sale.order` confirmed, window | One currency, window = `meta.period` | 200 | 200 | `WA.test_b_ceo_top_customers_…`; `test_top_customers_is_bounded_to_the_reporting_period` | CORRECTED AND VERIFIED |
| all four | Role gate (RPC mirrors the menu) | `_require_any_group` | Sales / Manager denied; Accountant / CEO / Owner admitted | as stated | as stated | `test_dashboard_service.py` (14 role tests) | VERIFIED |

### 3.3 Sales, HR, Warehouse

| Dashboard | KPI | Data source | Business definition | Expected | Actual | Test evidence | Status |
|---|---|---|---|---|---|---|---|
| Sales | Average deal size | `sale.order` confirmed in the period | Mean confirmed order value, company currency | (100 + 200 EUR) / 2 = 100 | 100 (was 150) | `KA.test_k_average_deal_size_is_in_one_currency` | CORRECTED AND VERIFIED |
| Sales | Pipeline funnel | `crm.lead` | Open value per stage | engine rows | engine rows | `test_department_dashboards.py` | VERIFIED |
| Sales | Salesperson leaderboard | `sale.order` confirmed, all time | Order value per salesperson, one currency | 200 | 200 | `WA.test_b_ceo_top_customers_and_sales_leaderboard_…` | CORRECTED AND VERIFIED (all-time by design; see §4) |
| HR | Headcount by department | `hr.employee` active | Active employees per department | 3 (the archived one has left) | 3 | `KA.test_h_headcount_counts_active_employees_per_department` | VERIFIED |
| HR | Employee turnover | `hr.employee` (`departure_date`, `create_date`) | departures / average headcount × 100 | hand-calculated roster | matches | `ncollection_core/tests/` KPI roster test | VERIFIED (see §4: hire date is `create_date`) |
| HR | Approved leave by type | `hr.leave` validated | Approved leave days per type | n/a | panel omitted | `hr_holidays` is not part of the CI / default plan module set | BLOCKED — SPECIFIC DATA DEPENDENCY (`hr_holidays`) |
| Warehouse | Inventory turnover | `stock.move` done to customers / `stock.quant` internal | Units shipped in the window / units on hand, **in the product's unit** | (0 + 12) / (0 + 12) | matches (was 1/12) | `KA.test_w_inventory_turnover_uses_units_not_move_lines` | CORRECTED AND VERIFIED |
| Warehouse | Movement velocity | `stock.move` done | Units moved per product (product unit) | 24 + 1 dozen = 36 | 36 (was 25) | `KA.test_w_quantities_are_added_in_the_products_own_unit` | CORRECTED AND VERIFIED |
| Warehouse | Stock valuation by product | `stock.quant.value` (stock_account), current company | Value of stock on hand | 12 × 5 = 60 | 60 (the panel was **silently omitted** before: it read `stock.valuation.layer`, which Odoo 19 does not have) | `KA.test_w_stock_valuation_reads_odoo_19_…` | CORRECTED AND VERIFIED |
| Warehouse | Valuation for a plain Warehouse user | `stock.quant.value` is Inventory-Manager only | Cost is not shown to a user Odoo does not allow to read it | panel omitted | omitted | provider returns `None` on `AccessError` | VERIFIED (by design) |
| Sales / HR / Warehouse | Role gate | `_require_role_or_technical_admin` | Own role only; Owner / CEO / Manager denied | as stated | as stated | `test_department_dashboards.py` (8 role tests) | VERIFIED |

### 3.4 Platform Summary

| Dashboard | KPI | Data source | Business definition | Expected | Actual | Test evidence | Status |
|---|---|---|---|---|---|---|---|
| Platform | Monthly revenue (MRR) | `ncollection.subscription.mrr` of active subscriptions | Monthly-normalised plan price, **one currency** (converted at today's rate) | 100 + (200 EUR = 100) = 200 | 200 (was 300) | `PA.test_m_monthly_revenue_adds_one_currency` | CORRECTED AND VERIFIED |
| Platform | Expiring soon | active subscriptions, `end_date` in [today, today + 30] | The next 30 days; a lapsed subscription is not "soon" | 1 of (past, +10, +45) | 1 (was 2) | `PA.test_e_a_lapsed_subscription_is_not_expiring_soon` | CORRECTED AND VERIFIED |
| Platform | Tenants (total / active / trial), active subscriptions, churn, conversion, failed provisioning, at-risk, storage | `ncollection.tenant`, `.subscription`, provisioning jobs, `pg_database_size` | Counts and point-in-time proxies (documented as such) | per `test_dashboard.py` | as expected | `ncollection_subscription/tests/test_dashboard.py` (11) | VERIFIED |
| Platform | Access | group + ORM | Platform Administrator only; the Tenant Owner has no access | denied | denied | `test_dashboard.py`, `ncollection_saas` Option A tests | VERIFIED |

### 3.5 Dashboards page (Odoo Spreadsheet Dashboards) and its Export

| Dashboard | KPI | Data source | Business definition | Expected | Actual | Test evidence | Status |
|---|---|---|---|---|---|---|---|
| Dashboards › Invoicing | Invoiced | `account.move.line` (Odoo formula) | Untaxed amount invoiced | SQL: 2,690 | 2,690 | browser (English and Arabic) vs a ledger query | VERIFIED |
| Dashboards › Invoicing | Invoices / Average invoice | Odoo formula | count / mean untaxed | 6 / 448.33 | 6 / 448 | browser vs SQL | VERIFIED |
| Dashboards › Invoicing | Unpaid, DSO, other Odoo-owned cards and the other six Odoo dashboards | Odoo spreadsheet formulas | defined by Odoo | — | — | not re-derived: the formulas belong to Odoo and are not ours to redefine | NOT IMPLEMENTED — EXPLICITLY OUT OF SCOPE |
| Dashboards | **Export** beside Share (Excel / PDF) | the snapshot of the page | The file equals what the page shows | cells, scorecards, chart values, filters | verified: Excel `PK` 8 KB, PDF `%PDF` 26 KB, names `Dashboard-<name>.<ext>` | `ncollection_dashboard_export/tests/test_export.py` (12); browser, English / Arabic, desktop / 375 px | VERIFIED |
| Dashboards | Share | Odoo's own button | — | present | present; Odoo hides Share and Export on **sample** dashboards (no data yet) | browser | VERIFIED (not changed) |

## 4. Limitations that need a product or accounting decision

1. **Order value is tax-inclusive** (`amount_total`) on Sales this month, Purchases, Top customers, Leaderboard and Average
   deal size. Revenue on the ledger dashboards is untaxed. Both are labelled by their own definition; unify only on a decision.
2. **Salesperson leaderboard is all-time**, and "Approved leave by type" is all-time; neither follows the page period.
3. **Employee turnover** uses `create_date` as the hire date (Odoo 19 community has no reliable hire date on `hr.employee`).
4. **Churn / trial conversion on the platform are point-in-time proxies** (no status-transition history yet); labelled as such.
5. The Odoo-native revenue **graph/pivot** on the platform adds plans of different currencies natively (not our model).
6. Cash is the **liquidity ledger** (bank and cash accounts). A customer payment awaiting bank confirmation is in outstanding
   receipts and is not cash yet, by design.

## 5. How to reproduce

```bash
# the audit tests, in a disposable database (never the shared stack)
odoo -d uxkpi -i hr,sale,purchase,stock,stock_account,ncollection_account_dashboard \
     --test-enable --test-tags /ncollection_account_dashboard,/ncollection_subscription:TestPlatformDashboardAccuracy,/ncollection_dashboard_export
```
