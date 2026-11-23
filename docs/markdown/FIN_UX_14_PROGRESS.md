# Financial UX — 14 requests (candidate `feature/fin-ux-14`, LOCAL ONLY)

Branch `feature/fin-ux-14` in worktree `D:\Projects\ncwt-acctset`, base `403b07a` (production). Not pushed, not deployed.
Dev runtime: container `nc_fx14_web` (127.0.0.1:8996; bridge + `ncollection-erp_nc_dbplane`, the dbplane network is
internal so a container on it alone publishes no port), addons = this worktree. Databases (all copies, all ours):
`fx14a` (UAE, Awtar), `fx14b` (UAE + branch + unrelated USD company), `fx14c` (Egypt on the generic chart), `fx14n`
(copy of the local `ncollection`, real bank data), `fx14t` (fresh CI-equivalent suite DB). Filestore volume `nc_fx14_fs`.

Local runtime synced to 403b07a on 2026-11-23 00:18 UTC: branch `release-403b07a` in D:\Projects\ncollection-erp;
ncollection / awtar / ncollection_demo `-u ncollection_core,ncollection_account_core`, rc=0, counts identical (backup
`D:\Backups\local-sync-403b07a-20261123T001339Z`).

## 1. Requests

| # | Request | Status | Where |
|---|---------|--------|-------|
| 01 | Duplicate Users block in General Settings | Done: hidden in tenants (never deleted), the platform DB shows it again | core `res_config_settings_users.xml`, saas same name |
| 02 | Customers / Suppliers lists | Done: Balance (company currency), Status, Open Invoices / Bills, contact columns (optional), Summary per row | account_reports `res_partner_financial.py`, `partner_financial_views.xml` |
| 03 | Summary smart button | Done: next to Invoiced, opens the same statement | same |
| 04 | Statement scope + filters | Done: scope-aware partner picker, scope switch clears partners (viewer) and the server drops partners of the other scope; Debit / Credit in the financial colours. Not done: a new filter bar layout / date presets / column picker for the statement viewer (the existing viewer's are kept) | account_reports `report_service.py`, `report_viewer.*` |
| 05 | Allocation Remove / Clear All | Done: row x, Clear All, Automatic; locked selection never re-applied; row lock on Confirm | account_core `account_payment.py`, `payment_workflow_views.xml` |
| 06 | Per-invoice Print Settings | Done: layout, template, sections & columns, stamp, payment history; Preview / Reset. Not done: bank details / payment terms / notes switches (no reliable cell names in Odoo's template), live in-dialog preview (Preview opens the real PDF) | account_core `move_print_setting.py`, branding hooks |
| 07 | Calculation Rules | Done: before tax (945) / after tax (950) via the adjustment engine, preview, UAE caveat; posted invoices unchanged | invoice_layout `calculation_rules.py` (+ wizards/) |
| 08 | Bank transactions: balance repair + redesign | Done: confirmation folded into its payment, value-date order, journal currency; Bank Confirmation rows gone; money columns coloured, compact table | journal_operations `journal_transaction.py` |
| 09.1 | Clickable Current Balance | Done: opens the rows that moved the account; last running = card figure | journal_operations `account_journal.py` |
| 09.2 | Financial colours + newest first | Done: one company setting, CSS variables; payments list id tie-break; contract test for operational lists | account_core `financial_palette.py`, `test_newest_first.py` |
| 10 | Tenant identity in XBRL | Done: scheme = profile parameter, else the tenant's address, else a URN of the database | account_reports `xbrl_export.py`, `xbrl_gl.py` |
| 11 | Payee empty -> Suspense | Done: company Unidentified Payments (Suspense) account, never bank Suspense / Outstanding / transfer / receivable / payable; clear error when unset | journal_operations `res_company.py`, `account_payment.py` |
| 12 | Five card buttons | Done: removed from the card face, moved to the card menu (nothing unreachable) | journal_operations dashboard view |
| 13 | Transfer exchange difference + bank fee | Done: fee entry + explicit exchange entry, full reconcile, preview, missing-rate refusal | journal_operations `internal_transfer.py` |
| 14 | Transfer receipt detail | Done on the form (Exchange Rate & Transfer Details, booked values). Not done: a printed transfer receipt (no report exists for transfers) | same view |
| 16 | Account Balances audit | Done: Opening / Debit / Credit / Closing; drill = ledger with running balance | account_reports `account_report_reference.py` |

## 2. Accounting rules (as implemented)

- Bank transactions: every posted line on the journal's account belongs to one row. A statement line that settles a
  posted payment of the same journal adds its booked amount to THAT payment's row (bank_date = latest such line). The
  running balance is the window sum over (value_date, id), value_date = bank_date or document date, in the journal
  currency for a foreign-currency journal. Last row = card Current Balance; at any value date = ledger balance. Known
  exception: a payment confirmed by several bank lines on different days counts on the last day.
- Account Balances: Closing = Opening + Debit - Credit; Opening is the Trial Balance / GL opening (balance-sheet
  accounts carry everything, P&L resets at the fiscal year, prior-year P&L rolls into current-year earnings). The
  report now refuses a period that crosses a fiscal year, like the TB / GL.
- Partner balance (lists, Summary button): posted receivable / payable items of the exact partner in the companies the
  user works in, company currency. Equal to the closing balance of the statement the Summary opens (tested).
- Suspense: only when nothing is chosen; reconcilable current asset / liability distinct from the transfer and bank
  Suspense accounts; cleared by a reclassification entry reconciled against it.
- Transfers: fee = its own entry in the source currency (Dr Bank Fees / Cr transfer account); the company-currency
  residual left on the clearing account is the genuine exchange difference (Odoo's exchange journal and gain / loss
  accounts); all clearing lines in one full reconcile. Sent = fee + received - gain (tested in 4 currency cases).
- Discount after tax: the discount line carries no tax; VAT on the undiscounted base, never understated.

## 3. Newest-first rule per list
Invoices / bills / entries: accounting date desc, then entry (Odoo's own _order). Payments: date desc, name desc, id
desc. Bank transactions: value date desc, id desc (running balance computed oldest first on the same key). Internal
transfers: date desc, id desc. A column sort is temporary (never stored); master data stays alphabetical.

## 4. Upgrade requirements (both platforms, every tenant database)
Version bumps: ncollection_core 19.0.1.36.0, ncollection_saas 19.0.6.33.0 (platform databases), ncollection_account_core
19.0.1.20.0, ncollection_account_reports 19.0.1.20.0, ncollection_journal_operations 19.0.1.3.0,
ncollection_account_invoice_layout 19.0.1.4.0. ncollection_branding: Python only (no bump; needs the restart).
Targeted `-u` per database: tenants `ncollection_core,ncollection_account_core,ncollection_account_reports,
ncollection_journal_operations,ncollection_account_invoice_layout` (each only where installed); platform databases also
`ncollection_saas`. Never `-u all`. Schema: new tables `ncollection_move_print_setting`,
`nc_payment_allocation_move_rel`; new company / payment / transfer columns; the transaction SQL view is recreated.
No data migration: existing discount types keep their stored tax mode; existing transfers have empty fee / exchange
fields; companies get the default palette and before-tax rule.

## 5. Production plan (DOCUMENTATION ONLY — not authorized)
Same procedure as 403b07a (`/root/deploy_403b07a.sh` pattern): verified backup of all 10 databases + filestores,
GitHub develop ff, rehearsal on copies of balancecloud / andalus / atlas-core, then Balance Cloud then Atlas Core,
counts before / after, parity, health, browser acceptance with the owner signed in. Rollback: code to 403b07a + `-u` of
the same modules, or full restore from the backup.
