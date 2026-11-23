# Financial UX — 14 requests (candidate `feature/fin-ux-14`, LOCAL ONLY)

Branch `feature/fin-ux-14` in worktree `D:\Projects\ncwt-acctset`, base `403b07a` (the deployed production baseline).
**Not pushed, not merged, not deployed.** Every change below needs the owner's explicit approval after local review.
Dev runtime: container `nc_fx14_web` (127.0.0.1:8996; bridge + `ncollection-erp_nc_dbplane`), addons = this worktree.
Databases (all copies, all ours): `fx14a` (UAE), `fx14b` (UAE + branch + unrelated USD company), `fx14c` (Egypt,
generic chart), `fx14n` (copy of local `ncollection`, real bank data), `fx14t` (CI-equivalent suite DB), `fx14base`.
Filestore volume `nc_fx14_fs`. Acceptance screenshots: `D:\Backups\fin-ux-14-acceptance\run3` (final code).

## 1. Requirements traceability

Status **Done** = implemented, covered by an automated test, and seen working on a real-browser screen (Chrome driven by
Playwright, fx14n). Commit = the commit that introduced it (review fixes landed later in `36b9002` and `64e133e`).

| # | Requirement | Status | Implementation | Commit | Automated tests | Browser (run3) | Remaining |
|---|---|---|---|---|---|---|---|
| 01 | Duplicate Users block in General Settings | Done | core `views/res_config_settings_users.xml`; saas re-shows it on the platform DB | 57a8434 | core `test_settings_users_block` | 18 | — |
| 02 | Customers / Suppliers list redesign | Done | account_reports `models/res_partner_financial.py`, `views/partner_financial_views.xml` | 31537e0 | `test_partner_financials` | 01, 02 | — |
| 03 | Partner Summary | Done | `action_nc_partner_summary` -> statement of that partner | 31537e0 | `test_partner_financials` (closing = list balance) | 03 | — |
| 04 | Customer / Supplier statement: scope + filters | Done | server: `report_service.py` scope domain, ineligible partners dropped, filter defaults sent; UI: compact bar (Period presets, From/To, Scope toggle, Partners, More filters with active count vs defaults) `report_viewer.{js,xml,scss}` | 31537e0, 3114763, 64e133e | account_reports suite (scope, opening / running / closing) | 04-07 (no overflow at 820 px) | presets are CALENDAR periods (labelled so), not fiscal |
| 05 | Payment allocation Remove / Clear All | Done | account_core `account_payment.py`, `payment_workflow_views.xml` | 580f77a | `test_allocation_remove_clear` | 12 | — |
| 06 | Per-invoice Print Settings | Done | `move_print_setting.py`, `account_move._nc_print_sections`, template switches (bank details + QR, payment terms, notes) | 11e0c8d, b7baa65 | `test_move_print_settings` (5: per document, company untouched, other invoice untouched, Odoo preview still renders) | 13, 14 | live preview inside the dialog: Preview opens the real PDF instead |
| 07 | Calculation Rules (discount before / after tax) | Done | invoice_layout `calculation_rules.py`, wizard, migration 19.0.1.4.0; after-tax refused for UAE / e-invoicing | 6445724, 36b9002 | `test_calculation_rules` | 15 | — |
| 08 | Bank transactions: balance repair + redesign | Done | journal_operations `journal_transaction.py` (SQL view); list shows the value date as Date | ba1a266, b2da2ec, 3114763 | `TestPostedBalanceAndCard`, `TestTransactionPalette` | 10 | a payment confirmed by bank lines on several days counts on the last day |
| 09.1 | Clickable Current Balance | Done | `account_journal.action_nc_ops_posted_balance` | b390f52 | `TestPostedBalanceAndCard` | 08, 09 (last running = card) | — |
| 09.2 | Financial colours + newest-first sorting | Done | account_core `financial_palette.py`; payments `default_order` | b2da2ec, 63d0d52 | `test_financial_palette`, `test_newest_first` | 10, 11 | a column sort lasts for the session (Odoo standard), it is not stored |
| 10 | Tenant identity in XBRL | Done | `xbrl_export._nc_entity_scheme` | bd8acdd | `test_xbrl_entity_identity` | — (file export) | — |
| 11 | No payee -> Suspense | Done | `res_company.nc_payment_suspense_account_id`, `account_payment` | 8e6c097, 36b9002 | `TestSuspenseFallback` | — | — |
| 12 | Five card buttons | Done | dashboard view: moved to the card menu | b390f52 | view install | 08 | — |
| 13 | Transfer bank fee + exchange difference | Done | `internal_transfer.py` fee entry + exchange entry + full reconcile | 63377fc, 36b9002 | `TestTransferFeesAndExchange` (4 currency cases) | 16 | — |
| 14 | Transfer details + printed receipt | Done | form card + `report/internal_transfer_receipt.xml`; `_nc_receipt_values` reads the POSTED entries, refuses when an entry is no longer posted, and checks the reconciliation independently | 63377fc, 58d7e30, 64e133e | receipt figures in 3 currency cases, read-only accountant prints, draft entry refused | 16, 17 | — |
| 16 | Account Balances audit | Done | `account_report_reference.py` Opening / Debit / Credit / Closing, drill to GL, cross-fiscal-year opening | df64349, 36b9002 | `test_account_balances_ledger` | — | — |

## 2. Accounting rules (as implemented)

- Bank transactions: every posted line on the journal's account belongs to one row. A statement line that settles a
  posted payment of the SAME journal is folded into that payment's row. Running balance = window sum over
  (value_date, id), where value_date = bank date or document date; the list shows, orders, filters and groups by it.
  Last row = card Current Balance (fx14n: −67.37 = 5,232.63 − 5,300.00).
- Account Balances: Closing = Opening + Debit − Credit; Opening as TB / GL (P&L resets at the fiscal year).
- Partner balance: posted receivable / payable of the exact partner in the CURRENT company, in company currency; equal
  to the closing balance of the statement the Summary opens.
- Transfers: the fee is its own entry (Dr Bank Fees / Cr transfer account). The company-currency residual is the genuine
  exchange difference (Odoo's exchange journal and accounts). All clearing lines are in ONE full reconcile.
  Receipt: value sent = fee + value received − gain (+ loss), read from the posted entries; refused if any entry is no
  longer posted.
- Discount after tax: refused for UAE companies and wherever UAE e-invoicing is installed (FTA: a discount at supply
  reduces the taxable value).

## 3. Test evidence (fx14t, clean CI-equivalent database)
- Focused: 457/457 (account_core settings + print settings, journal_operations, account_reports) after the review fixes.
- Wider: see the release report (all tests of the 7 touched modules).
- Pre-existing, NOT regressions:
  - `TestTranslationCoverage` lists 5 terms, all in files byte-identical to 403b07a:
    - `ncollection_saas/models/provisioning_job.py` (×3);
    - `ncollection_uae_einvoicing` inbound selection (×2): the terminology layer stores these as "Supplier" while the
      catalogue holds the "vendor" msgid.
  - `TestApprovalGate` fails on 403b07a too.
- Gates: flake8 clean, invariants clean, doc counts clean, role matrix clean, architecture guard clean (80 files),
  pylint-odoo 205 = baseline.
- Data safety:
  - posted-accounting snapshots (moves, lines, payments, partials, per-account balance digest) are identical before and
    after the upgrade on fx14a / fx14b / fx14c;
  - fx14n is identical to the live local `ncollection`.

## 4. Upgrade requirements (both platforms, every tenant database)
Versions:

| Module | Version | Note |
|---|---|---|
| ncollection_core | 19.0.1.36.0 | |
| ncollection_saas | 19.0.6.33.0 | platform databases |
| ncollection_account_core | 19.0.1.21.0 | |
| ncollection_account_reports | 19.0.1.21.0 | |
| ncollection_journal_operations | 19.0.1.4.0 | |
| ncollection_account_invoice_layout | 19.0.1.4.0 | post-migrate: discount types follow the company rule |
| ncollection_branding | no bump | Python only; needs the restart |

Procedure:
- Targeted `-u` per database, never `-u all`:
  - tenants: `ncollection_core,ncollection_branding,ncollection_account_core,ncollection_account_reports,
    ncollection_journal_operations,ncollection_account_invoice_layout` (only where installed);
  - platform databases: the same list plus `ncollection_saas`.
- Then RESTART the Odoo service (new Python). An upgrade run in a separate process does not reload the serving workers;
  locally, "method action_nc_print_settings does not exist" appeared until the restart.

Schema:
- new tables: `ncollection_move_print_setting`, `nc_payment_allocation_move_rel`, `ncollection_invoice_calculation_rules`;
- new company / payment / transfer columns;
- the transaction SQL view is recreated.

No posted entry is rewritten.

## 5. Production plan (DOCUMENTATION ONLY — not authorized)
See the release folder `PRODUCTION_PLAN.md`. Rollback: code back to 403b07a plus `-u` of the same modules (the new
tables and columns stay, unused), or a full restore from the pre-deploy backup.
