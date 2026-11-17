# XBRL export

Reports: **Balance Sheet, Profit and Loss, Cash Flow**. Profile: **IFRS Accounting Taxonomy 2025, Full IFRS** (owner decision 2026-11-16). Entry point referenced by
`schemaRef`, never copied into the repository:
`https://xbrl.ifrs.org/taxonomy/2025-03-27/full_ifrs_entry_point_2025-03-27.xsd`. Nothing from the taxonomy is
redistributed, so no packaging licence question arises. Validator: **Arelle `arelle-release==2.37.78`**, run in a
throw-away container (it fetches the taxonomy over HTTPS); it is not part of the Odoo image.

Code: `ncollection_account_reports/models/xbrl_export.py`; route `/ncollection/account_reports/xbrl/<model>/<id>`
(same guards as XLSX: engine models only, read access checked as the user, `Cache-Control: private, no-store`).
The facts are the report's OWN figures (`_nc_bucket_totals` / `_nc_pl_figures`), the same code path as the screen,
PDF and Excel. Nothing is written.

## What a file says, and what it does not

* Entity: the company's TRN / VAT when set, otherwise `company-<id>`, scheme `https://www.ncollection.com/xbrl/entity`.
* Period: instant (Balance Sheet) or start / end (P&L), plus the report's comparison period when one is selected.
* Unit `iso4217:<company currency>`, `decimals` = the currency's decimal places, signs exactly as the report.
* It is **not** a filing. It does not claim the statements comply with IFRS, and it has not been tested against any
  regulator's rules (the UAE has no XBRL filing mandate for these statements that this was built for). A
  jurisdiction profile (IFRS for SMEs, a regulator's extension) is a new mapping table, not new code.

## Mapping

| Report line | Concept | Exported |
|---|---|---|
| Current Assets | `ifrs-full:CurrentAssets` | yes |
| Fixed & Other Assets | `ifrs-full:NoncurrentAssets` | yes |
| TOTAL ASSETS | `ifrs-full:Assets` | yes |
| Current Liabilities | `ifrs-full:CurrentLiabilities` | yes |
| Long Term Liabilities | `ifrs-full:NoncurrentLiabilities` | yes |
| (sum of the two) | `ifrs-full:Liabilities` | yes |
| Equity + Accumulated Earnings | `ifrs-full:Equity` | yes |
| TOTAL LIABILITIES & EQUITY | `ifrs-full:EquityAndLiabilities` | yes |
| Sales Revenue | `ifrs-full:Revenue` | yes |
| Other Operating Income | `ifrs-full:OtherIncome` | yes |
| Cost of Goods Sold | `ifrs-full:CostOfSales` | yes |
| NET PROFIT | `ifrs-full:ProfitLoss` | yes |
| GROSS PROFIT | (none) | **no**: the report's figure includes other income; IFRS GrossProfit is revenue minus cost of sales |
| Operating Expenses | (none) | **no**: no single equivalent concept in the full taxonomy |

## Export surfaces

| Surface | PDF | Excel | XBRL |
|---|---|---|---|
| Balance Sheet | yes | yes | **yes** |
| Profit and Loss | yes | yes | **yes** |
| Trial Balance, General Ledger, Partner Ledger, Partner Statement, Aged Balances | yes | yes | no: account-level listings with no IFRS concept per row (a "mapping" would be invented) |
| Cash Flow (indirect) | yes | yes | **yes**: ProfitLoss, AdjustmentsForDepreciationExpense, CashFlowsFromUsedIn{Operating,Investing,Financing}Activities, IncreaseDecreaseInCashAndCashEquivalentsBeforeEffectOfExchangeRateChanges, CashAndCashEquivalents (opening and closing instants). Not exported: "Movement in working capital" (no single concept) |
| Changes in Equity | yes | yes | **partial: the total-equity column**: `Equity` at the opening instant (day before the period) and the closing instant, `ProfitLoss` (movement of the accumulated result), `ChangesInEquity` (total movement); no dimension, so the facts mean "equity as a whole". **Not exported: the per-component columns** (ComponentsOfEquityAxis members). The report's components are Odoo account TYPES ("equity" holds share capital, reserves and, on many charts, retained earnings; "equity_unaffected" is the current-year earnings account) while IFRS members are economic components (IssuedCapitalMember, RetainedEarningsMember, OtherReservesMember, ...): the books keep no account-to-member mapping, so any member would be invented. Nor the split of the non-profit movement (dividends, share issues, transfers): the ledger does not classify those. Supporting them needs a per-account component mapping maintained by the accountant (a business decision; not built) |
| Tax Analysis, UAE VAT report | yes | yes | no: tax returns are not IFRS statements |
| Financial Summary, Profitability, Revenue / Expense Analysis, Dimension Analysis, Budget vs Actual, Asset Register | yes | yes | no: management analyses, no IFRS concept |
| Sales / Invoicing premium dashboards, Spreadsheet dashboards export | yes | yes | no: operational data |

Unsupported reports have no XBRL button, and the route answers 404 for them.

**Coverage, stated accurately:** three statements complete (Balance Sheet, Profit and Loss, Cash Flow), one partial
(Changes in Equity: total column only), every other report not applicable for the reason in the table. The XBRL
requirement is therefore **partially complete**, not "fully complete".

## Evidence (restored copy of `ncollection`, 2026-11-16)

| File | Arelle errors | Arelle warnings | Reconciled with the report |
|---|---|---|---|
| Balance Sheet (2026-11-16, comparison 2025-11-16) | 0 | 0 | Assets 1,278.25 = report; Equity & Liabilities 1,278.25 = report |
| Profit and Loss (2026-01-01 – 2026-11-16, comparison previous year) | 0 | 0 | Revenue 8,670.50 · Other Income 0.00 · Cost of Sales 53.12 · Profit 6,335.47, all = report |
| Cash Flow (2026-01-01 – 2026-11-17, re-run 2026-11-17 with BS and P&L) | 0 | 0 | Operating 2,400.00 · Investing 0 · Financing 0 · Net change 2,400.00 · Profit 6,335.47 · Cash 0.00 → 2,400.00, all = report |
| Changes in Equity (2026-01-01 – 2026-11-17, 2026-11-17 rehearsal copy) | 0 | 0 | Opening 0.00 · Profit 6,335.47 (= P&L) · Total change 6,335.47 · Closing 6,335.47 (= Balance Sheet equity), all = report. The first build used `IncreaseDecreaseInEquity`, which is NOT in the taxonomy: Arelle rejected it, the concept is `ChangesInEquity` |
| Negative control (one invented concept) | 2 (`xmlSchema:elementUnexpected`, `xbrl:schemaImportMissing`) | — | proves the taxonomy was really loaded |

Downloaded through a real browser session from the route. Automated tests: `tests/test_xbrl_export.py` (facts equal
the report and the independently summed income ledger, comparison context, no GrossProfit, route refusals).
