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

## Export menu integration (owner review 2026-11)

The report viewer (every NCollection financial report opened with View) has ONE Export menu: Excel (.xlsx), PDF,
XBRL (.xbrl). It is driven by the report service (`nc_report_run` returns `xbrl_unavailable`, `nc_report_export`
accepts `xbrl`), so every report reached through the viewer shows XBRL the same way:

* a report with an IFRS mapping downloads the instance (same wizard record, same filters, dates, company, currency);
* every other report shows "XBRL (.xbrl) — not available" DISABLED, with the reason underneath; asking the service
  anyway is refused with the same reason. No `.xbrl` file is ever produced for it.

| Report | PDF | Excel | XBRL in Export menu | Taxonomy / mapping | Limitation |
|---|---|---|---|---|---|
| Balance Sheet | yes | yes | download | IFRS 2025 Full: CurrentAssets, NoncurrentAssets, Assets, CurrentLiabilities, NoncurrentLiabilities, Liabilities, Equity, EquityAndLiabilities | — |
| Profit and Loss | yes | yes | download | Revenue, OtherIncome, CostOfSales, ProfitLoss | Gross Profit / Operating Expenses not exported (no equivalent concept) |
| Cash Flow | yes | yes | download | Operating / Investing / Financing, net change before FX, depreciation, cash instants | working-capital line not exported |
| Changes in Equity | yes | yes | download | Equity (opening / closing), ProfitLoss, ChangesInEquity; per ComponentsOfEquityAxis member when every equity account with a balance has a Component of Equity | components only when mapped (Accounting > Chart of Accounts > account form > Component of Equity); unmapped: total column |
| Trial Balance, General Ledger, Partner Ledger, Partner Statement, Aged Balances | yes | yes | disabled + reason | — | account-level listings: no IFRS concept per row |
| Tax Analysis | yes | yes | disabled + reason | — | a tax return analysis; needs a tax authority taxonomy |
| UAE VAT report | yes | yes | disabled + reason | — | the FTA publishes no XBRL taxonomy for the VAT return |
| Financial Summary, Profitability, Revenue / Expense, Cost / Profit Centre, Department, Comparison, Executive | yes | yes | disabled + reason | — | management analyses |
| Dashboards (workspace, spreadsheet) | yes | yes | not offered | — | operational data, not a financial statement |

Equity components (`account.account.nc_equity_component`): Issued capital, Share premium, Treasury shares, Retained
earnings, Other reserves. The current-year earnings account and the profit and loss accounts are retained earnings by
nature. A ledger with ANY unmapped equity account carrying a balance exports the total column only, so the components
always add up to the total (tested).

## Coverage totals (acceptance review of c340bfa, 2026-11-18)

21 reports are opened through the report viewer (every `nc_report_model` action). Each shows XBRL in its Export menu:
a download, or a disabled entry with the reason below. No report produces a file it cannot stand behind.

| Class | Count | Reports |
|---|---|---|
| Fully supported and validated (Arelle 0 errors, figures = report) | 4 | Balance Sheet, Profit and Loss, Cash Flow, Changes in Equity (total column) |
| Supported with a required accounting mapping | 1 (extension of the above) | Changes in Equity per component: needs "Component of Equity" on every equity account with a balance; without it, total column only |
| Applicable, not yet implemented | 2 | Aged Balances (IFRS 7 ageing of receivables / payables), Asset Register (IAS 16 property, plant and equipment reconciliation) |
| Not applicable with the IFRS taxonomy | 15 | Trial Balance, General Ledger, Partner Ledger, Partner Statement, Report Reference, Tax Analysis, UAE VAT Return, Financial Summary, Profitability, Revenue Analysis, Expense Analysis, Cost Centres, Profit Centres, Departments, Budget vs Actual |

**Trial Balance / General Ledger / Partner Ledger:** IFRS has no concept per account row. A valid path exists in a
DIFFERENT taxonomy, XBRL Global Ledger (XBRL GL, gl-cor / gl-bus), which carries journal entries and account
balances; it is not IFRS, is not built, and would be a separate decision. **Tax Analysis / UAE VAT:** the FTA publishes
no XBRL taxonomy for the VAT return. **Management analyses:** no taxonomy concept means the same thing.

So: 4 of 21 export XBRL today (one with an optional component extension), 2 are buildable IFRS work, 15 are not IFRS
statements. The ERP is NOT "XBRL-complete"; it is complete for the four primary financial statements.


## XBRL Global Ledger: Trial Balance, General Ledger, Partner Ledger (owner review 2026-11-18)

Ledger listings now export a real XBRL instance in XBRL International's **Global Ledger** taxonomy (XBRL GL 2015-03-25
Recommendation, Palette entry point `plt/case-c-b-m-u-t/gl-plt-all-2015-03-25.xsd`, public and freely usable), not
IFRS: the account is DATA there (`accountMainID` / `accountMainDescription`), never an invented concept.
Code: `models/xbrl_gl.py`.

- **Trial Balance**: `entriesType` = `trialbalance`, shaped like the Recommendation's own sample `ids/BP_TrialBalance.xml`:
  per account a beginning balance, the period debit and the period credit (`period_change`, D / C) and an ending balance.
  Amounts are never negative; the sign is the debit / credit code.
- **General Ledger / Partner Ledger**: `entriesType` = `ledger`; per account (per partner) one `entryHeader` with the
  beginning balance, every journal item of the period (account, partner as `identifierReference`, entry number as
  `documentNumber`, posting date, label, D / C) and the ending balance.
- The figures are the report's own rows under the report's filters (journals, accounts, partners, posted / all, zero
  rows); the filters are written into `entriesComment`.

Evidence (restored copies, 2026-11-18): Arelle 2.37.78 with the official taxonomy, **0 errors** on `ncollection` and
`awtar`; an independent parse reconciled every fact with the report (21 accounts / 84 facts; with the journal filter
Ahly Bank + Main Cash, 14 accounts / 56 facts, totals 64,293.39 = the screen); a negative control (an invented element
and an invalid D/C code) gives `xmlSchema:elementUnexpected` / `xbrl:schemaImportMissing` / `xmlSchema:valueError`, so
the taxonomy was really loaded. Downloaded in the real browser from the viewer's Export menu (filename
`Trial Balance.xbrl`, `application/xml`). Not a regulatory filing: no regulator publishes an XBRL GL profile for these.

**Coverage now:** 7 of 21 viewer reports export XBRL (4 IFRS statements + 3 XBRL GL ledgers); 2 are applicable IFRS
work not built (Aged Balances, Asset Register); 12 have no taxonomy that means the same thing (management analyses,
the VAT return the FTA files without XBRL, Tax Analysis, the customer / supplier statement — use the Partner Ledger).
