# XBRL export

Profile: **IFRS Accounting Taxonomy 2025, Full IFRS** (owner decision 2026-11-16). Entry point referenced by
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
| Cash Flow, Changes in Equity | yes | yes | not yet: mappable (`CashFlowsFromUsedInOperatingActivities`, …) but the report buckets need their own reviewed map; next profile step |
| Tax Analysis, UAE VAT report | yes | yes | no: tax returns are not IFRS statements |
| Financial Summary, Profitability, Revenue / Expense Analysis, Dimension Analysis, Budget vs Actual, Asset Register | yes | yes | no: management analyses, no IFRS concept |
| Sales / Invoicing premium dashboards, Spreadsheet dashboards export | yes | yes | no: operational data |

Unsupported reports have no XBRL button, and the route answers 404 for them.

## Evidence (restored copy of `ncollection`, 2026-11-16)

| File | Arelle errors | Arelle warnings | Reconciled with the report |
|---|---|---|---|
| Balance Sheet (2026-11-16, comparison 2025-11-16) | 0 | 0 | Assets 1,278.25 = report; Equity & Liabilities 1,278.25 = report |
| Profit and Loss (2026-01-01 – 2026-11-16, comparison previous year) | 0 | 0 | Revenue 8,670.50 · Other Income 0.00 · Cost of Sales 53.12 · Profit 6,335.47, all = report |
| Negative control (one invented concept) | 2 (`xmlSchema:elementUnexpected`, `xbrl:schemaImportMissing`) | — | proves the taxonomy was really loaded |

Downloaded through a real browser session from the route. Automated tests: `tests/test_xbrl_export.py` (facts equal
the report and the independently summed income ledger, comparison context, no GrossProfit, route refusals).
