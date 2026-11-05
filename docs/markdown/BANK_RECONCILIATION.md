# Bank Transactions & Reconciliation (Community)

`ncollection_bank_reconciliation` 19.0.1.0.0 — branch `feature/bank-reconciliation`. Status: implemented and verified on
disposable databases; **not installed on any shared database** (that needs the separate release authorisation).

## 1. The defect, and the decision

The Accounting dashboard's bank card had a **Transactions** button that opened an empty *Bank Statements* list:
Odoo 19 Community ships no bank-matching workspace (the Enterprise `account_accountant` owns it).

**Decision: ADOPT the OCA engine, wrap it natively** — the same pattern as `account_asset_management` (#122).

* `OCA/account-reconcile` → `account_reconcile_oca` (the split-panel matching workspace; every match is Odoo's own
  `account.move.line.reconcile()`), `account_statement_base`.
* `OCA/bank-statement-import` → `account_statement_import_base`, `_file`, `_file_reconcile_oca`, `_camt`,
  `_sheet_file`, `_sheet_file_xlsx`, `_qif`. The `_online_*` providers are **not** installed (no bank sync).
* `ncollection_bank_reconciliation` adds what the engine lacks (section 4). No Enterprise code was read or copied; the
  functional reference is the public Odoo 19 documentation.

Why not BUILD: a reconciliation engine is core accounting (partials, exchange differences, write-offs, lock dates);
OCA's is maintained, uses Odoo's own `reconcile()`, and its 48 tests pass on our stack. Why not Enterprise: licence.

## 2. OCA evaluation (pins resolved 2026-11-05)

| Module | Version | Licence | Status | Depends | Upstream tests on our stack |
|---|---|---|---|---|---|
| `account_reconcile_oca` | 19.0.1.1.2 | **AGPL-3** | maintained (etobella) | `account_statement_base`, `web_widget_*` none | 48/48 |
| `account_statement_base` | 19.0.1.0.1 | LGPL-3 | Mature | `account` | pass |
| `account_statement_import_base` | 19.0.1.0.0 | LGPL-3 | Mature | `account_statement_base` | pass |
| `account_statement_import_file` | 19.0.1.0.0 | LGPL-3 | Mature | `_base` | pass |
| `account_statement_import_file_reconcile_oca` | 19.0.1.0.0 | **AGPL-3** | — | `_file`, `account_reconcile_oca` | pass |
| `account_statement_import_camt` | 19.0.1.0.0 | **AGPL-3** | — | `_file` | pass |
| `account_statement_import_sheet_file` | 19.0.2.0.2 | **AGPL-3** | — | `_file` (python `xlrd`, in the image) | pass |
| `account_statement_import_sheet_file_xlsx` | 19.0.2.0.0 | **AGPL-3** | — | `_sheet_file` (`openpyxl`, in the image) | pass |
| `account_statement_import_qif` | 19.0.1.0.0 | **AGPL-3** | — | `_file` | pass |

Pins: `OCA/account-reconcile@e3d99f3d` (2026-10-31), `OCA/bank-statement-import@aa647ee5` (2026-10-30). Combined with
the full CI module set: **3,439 tests, 0 failed, 0 errors** (`uxbankfull`, disposable). With our module: OCA's
reconcile + import suites and ours together **102/102**.

**Licensing.** Five of the new modules are AGPL-3. The platform already ships AGPL-3 OCA modules
(`auditlog`, `account_asset_management`, `mis_builder`, `helpdesk_mgmt`, `account_financial_report`, `report_xlsx`,
`auth_session_timeout`), so this adds **no new licence class**; the obligation that comes with AGPL-3 in a hosted
product (offer the corresponding source of the AGPL modules, including modifications, to network users) already
applies today and is unchanged in kind. We do not modify OCA code (everything is an `_inherit` in our own LGPL-3
module). The LGPL-3-wrapper-over-AGPL-3 question recorded in `OCA_DEPENDENCIES.md` (#122) now covers one more module;
it remains a legal question this work did not settle.

**Known upstream issues checked:**

| Issue | Status upstream | Our handling |
|---|---|---|
| #1036 `KeyError 'original_amount_unsigned'` in "full paid" | open, present at the pin | **fixed** in our override, test `test_full_paid_on_a_line_allocated_in_full_is_a_no_op` |
| #995 "false exchange difference" on same-currency foreign statements | open, AI-diagnosed, no maintainer reply | **not a false difference**: an invoice booked at one rate and paid at another has a real gain/loss in company currency. Test 10 proves it is booked **once**, the invoice closes in both currencies, entries balance. No override (changing it would be wrong) |
| #993 / #1011 (16.0) manual counterpart amount with late rates | 16.0 only | not reproduced on 19.0 in our scenarios; listed as a watch item |

## 3. Defects found during this work (not in any upstream issue)

| # | Where | Defect | Fix |
|---|---|---|---|
| D1 | engine `reconcile_bank_line` | never checks `can_reconcile`: an RPC call books what the button hides (Rule 4) | server-side refusal |
| D2 | engine `_unreconcile_bank_line_edit` | rebuilds the pending match **before** undoing, from the old items: the next Validate re-books the released counterpart as an unmatched receivable (balanced, but the invoice stays open and the customer shows a credit too) | rebuild after the undo; test 13 asserts no stale line |
| D3 | engine | an overpayment's remainder sits on the customer's receivable with Validate disabled, and re-selecting the same account changes nothing (stuck) | the engine's own creation-time rule applied consistently (edit mode, partner's own receivable/payable only) |
| D4 | import `_match_journal` | `ilike` on the account number: a file for `1234` lands in the `41234` journal | exact match |
| D5 | import | no active-company check on the matched journal | refused unless the active company |
| D6 | import (CAMT, CSV/XLSX without an id column) | **no duplicate protection at all**: CAMT keeps the bank reference only in `ref`; a re-imported file is booked twice | deterministic fingerprint (journal, date, amount, label, reference, partner account, occurrence) |
| D7 | QIF | fails with "Missing currency" on a journal in the company currency (`journal.currency_id` only) | effective journal currency |
| D8 | CSV/XLSX | rows in another currency are dropped silently | kept as OCA designed (one export, one journal per currency), but **listed in the preview** |
| D10 | engine auto-match | proposes a document only at its FULL open amount: an early-payment-discounted receipt is never proposed, so the discount could not be offered on a transaction's first opening (found by the browser suite) | discounted-amount proposal (unique candidate only), flag read after the engine's stored auto-match; regression test `test_early_discount_offered_on_the_first_opening` |
| D9 | translations | `account_reconcile_oca` ships 0 / 128 Arabic terms | OCA view / field / menu / JS terms in our catalogue (310 entries in all); the 56 OCA **Python** messages through `i18n_loader.py` + `i18n_oca/` |

## 4. What `ncollection_bank_reconciliation` adds

* **Card** (Accounting dashboard, bank and cash journals): *Transactions* (Odoo's own button → the workspace, all
  lines, reconciled included), *To Reconcile*, *Statements* (the native statement list, kept separate), *New* (manual
  transaction), *Upload* (bank only). Each is a journal method; the journal is `self`, never an id from the client.
* **Locking**: `FOR UPDATE NOWAIT` on the transaction and its counterparts inside a savepoint; a held lock is refused
  at once with a clear message (proven with two real database sessions: `e2e/bank/concurrency.py`).
* **No double reconciliation**: a reconciled transaction, or a fully reconciled counterpart, cannot be used again.
* **Company**: a counterpart of another company is refused.
* **Audit**: every match and every undo posts a note on the transaction's journal entry (user, documents). Undo never
  deletes a posted entry (edit mode is Odoo's `action_undo_reconciliation`; keep mode reverses).
* **Import**: preview of the exact file (SHA-256) before Import on every user entry point; exact journal; active
  company; the card's journal only; fingerprints; skipped-currency rows listed. One database transaction per import:
  all or nothing.

## 5. Functional parity with Odoo 19 Enterprise (documentation: bank/transactions, reconciliation, reconciliation_models)

Status words: **VERIFIED** = implemented and proven (automated test and/or browser); **EXTERNAL** = needs a provider
contract or a separately licensed service; **NOT IMPLEMENTED** / **NOT VERIFIED** = said as such.

| Feature | Enterprise reference | OCA support | NCollection implementation | Test evidence | Remaining limitation |
|---|---|---|---|---|---|
| Card *Transactions* → Bank Matching | journal card | workspace (`account_reconcile_oca`) | button routed to the workspace | browser 17-suite | — **VERIFIED** |
| All lines incl. reconciled / *Not Matched* | filter | yes | *Transactions* / *To Reconcile* | browser | — **VERIFIED** |
| Split view, global balance | yes | yes | OCA layout + phone layout | browser EN/AR/375 px | — **VERIFIED** |
| Manual transaction (*New*) | yes | form | card *New* | browser | — **VERIFIED** |
| Import CAMT.053 | yes | `_camt` | + exact account, dedup fingerprint (CAMT had none) | `test_camt053_*` | — **VERIFIED** |
| Import CSV / XLSX with mapping | yes | `_sheet_file(_xlsx)` | + preview, totals, all-or-nothing on other-currency rows | `test_csv_*` | — **VERIFIED** |
| Import QIF | yes | `_qif` | + company-currency journals | `test_02`, `test_12`, browser | — **VERIFIED** |
| Import OFX / QFX | yes | **none on 19.0** (PR #924 open, needs `ofxparse`) | own dependency-free parser, FITID dedup, strict | `test_ofx_*` | — **VERIFIED** |
| Import CODA (Belgium) | yes | none on 19.0 (OCA `l10n-belgium` has 18.0 only; its parser `pycoda` is GPLv3+, Python 2 / 3.5 era) | refused with a clear "format not supported" | evidence assessment §10 | **NOT IMPLEMENTED**: the Febelfin specification is public, but there is no 19.0 module, no compatible licence for the only parser and no openly licensed reference file to verify one against (§10) |
| Drag-and-drop file on the card / workspace | yes | no | `static/src/js/bank_drop.js` hands the file to the SAME wizard (`action_nc_bank_drop`): server-side type / size / bank-journal / access checks, preview and Import still required | `TestDrop` (6), `acceptance_sales.js` (card, workspace, refusals, Arabic RTL) | touch devices have no drag: Upload — **VERIFIED** |
| Preview before import | test-before-import for CSV | no | every format, exact file (SHA-256) | tests + browser | — **VERIFIED** |
| Duplicate prevention at import | yes | id-based only | bank id or fingerprint | `test_12`, `test_ofx_*`, `test_camt_*` | — **VERIFIED** |
| *Find Duplicate Transactions* (after the fact) | yes | no | Duplicate review: explained candidates, *Not a duplicate*, never deletes | `test_duplicate_review_*`, browser | deletion of a real duplicate stays the standard Odoo action — **VERIFIED** |
| Match invoices / bills / payments / outstanding items | yes | yes | + locking, no double use | tests 3-8, browser | — **VERIFIED** |
| Partial / many-to-one / one-to-many | yes | yes | — | tests 4-6 | — **VERIFIED** |
| Overpayment → customer credit; vendor advance | yes | partial (stuck Validate) | remainder on the partner's account is bookable | tests 7-8 | — **VERIFIED** |
| Set Account / write-off / bank fees / interest | yes | yes | — | tests 1, 9; browser (Bank Fees model) | — **VERIFIED** |
| Internal transfer | Internal Transfers model | yes | — | test 11; browser (both legs, transfer account nets 0) | — **VERIFIED** |
| Exchange difference | yes | yes (Odoo exchange entries) | independent audit, 9 scenarios | `TestForeignCurrencyAudit`; browser | — **VERIFIED** |
| Early-payment discount | discounted match suggested | **none** (and the engine never proposes a discounted amount) | discounted-amount PROPOSAL (one candidate or none) + *Apply early payment discount* with Odoo's own discount computation (base / tax / exchange), only inside the window, only the exact discounted total | `TestDiscountCoverage` (14: included / excluded / **mixed**, AED and **EUR at another rate**, window boundaries, partial, wrong amount, **credit note**, **vendor bill**, undo + re-reconcile, ambiguity) with hand-computed figures; browser | — **VERIFIED** (§10c) |
| Batch payments | `account_batch_payment` | none on 19.0 | `ncollection.bank.payment.batch` + *Match a batch* | `test_batch_*`; browser | no bank-file (SEPA/ISO) generation for batches — **VERIFIED for matching** |
| Sales orders in the matching | *Sales* action: orders → invoice → match | no | `ncollection_bank_reconciliation_sale` (§10a): the customer's confirmed orders, Odoo's own invoicing (regular / down payment), explicit posting, then ordinary matching | `TestSaleMatch` (17), `acceptance_sales.js` | invoice grouping is Odoo's default (one invoice for several orders of a customer) — **VERIFIED** |
| Upload bills for digitisation (OCR) | IAP service | no | — | — | **EXTERNAL** (Odoo IAP / OCR provider) |
| Bank synchronisation (Ponto, Plaid, …) | Odoo online sync | `_online_*` providers (not installed) | not offered; card text says so | — | **EXTERNAL** (provider contract + credentials) |
| Reconciliation models: manual / automatic, journals, partners, amount, label (contains / regex), counterparts fixed / % / regex | yes | yes (native models) | — | test 9, OCA suite, ambiguous-match test | — **VERIFIED** |
| Never auto-post an ambiguous match | implicit | yes | — | `test_automatic_matching_never_posts_an_ambiguous_match` | — **VERIFIED** |
| *To check* | yes | yes | — | OCA suite | — **VERIFIED (OCA)** |
| Undo | yes | yes (stale data bug) | fixed + audited | test 13, FX audit, browser | — **VERIFIED** |
| Concurrent matching | n/a | no | `NOWAIT` locks | two real sessions | — **VERIFIED** |
| Arabic UI | yes | 0 % | 318 + 59 interface terms, 56 Python messages, bridge module `i18n/ar.po` | browser AR, `test_oca_python_messages_have_arabic` | the two CAMT schema codes stay as identifiers |

## 6. Accounting acceptance (disposable fixtures; every figure read back from Odoo's own fields)

| # | Scenario | Result |
|---|---|---|
| 1 | Manual deposit to an account | balanced, +500 on the bank account |
| 2 | Imported withdrawal (QIF) | preview required, 3 lines, two identical lines both kept |
| 3 | Exact invoice payment | residual 0, invoice paid |
| 4 | Partial customer payment | residual 600, `partial` |
| 5 | One payment, two invoices | both 0 |
| 6 | Two payments, one invoice | 400 → 0 |
| 7 | Customer overpayment | invoice 0, open customer credit −200 on the partner; `nc_overpaid_amount` stays 0 (only a registered payment stamps it — documented semantics) |
| 8 | Vendor prepayment | open debit 300 on the vendor, later settles the bill |
| 9 | Bank fee via a model | +25 on the fees account |
| 10 | Exchange difference | 50.00 gain booked once; residual 0 in both currencies |
| 11 | Internal transfer | transfer legs net 0, no income / expense |
| 12 | Duplicate import | 0 new on re-upload, overlap imports only the new line |
| 13 | Undo | residual back, audit notes, no posted entry deleted, rematch works |
| 14 | Already reconciled | refused; the invoice is not paid twice |
| 15 | Other company / read-only user | refused (UserError / AccessError) |
| 16 | Concurrent matching | two real sessions: refused while locked, booked once after |
| 17 | Cash journal | +200 on cash |
| 18 | Arabic / English / mobile | browser suite |

## 6a. Independent foreign-currency audit (OCA/account-reconcile#995)

Fixture: company **AED**, bank journal **EUR**, transaction EUR, invoice EUR; rates set per date (D1 1 EUR = 4.00 AED,
D2 1 EUR = 4.40 AED); every expected figure computed by hand. `tests/test_fx_audit.py`, 9/9. Actual entries (excerpt
of the `FXAUDIT` output):

| # | Scenario | Expected | Actual entries | Result |
|---|---|---|---|---|
| 1 | same rate (invoice and receipt on D2) | no exchange entry | bank 101407 +440.00 / 100 EUR; receivable −440.00 / −100 EUR; **no EXCH entry** | PASS |
| 2 | invoice D1 (400.00), receipt D2 (440.00) | gain 40.00 once | bank +440.00; receivable −440.00; EXCH: receivable +40.00 / gain 441000 −40.00 | PASS |
| 3 | invoice D2, receipt D1 | loss 40.00 once | EXCH: receivable −40.00 / loss 641000 +40.00 | PASS |
| 4 | partial: 60 of 100 EUR | gain 24.00; 40 EUR / 160.00 AED open | bank +264.00 / 60 EUR; EXCH +24.00 / −24.00; invoice residual 160.00 / 40.00 | PASS |
| 5 | repeated attempt | refused, still 40.00 | one EXCH entry | PASS |
| 6 | undo, then rematch | 0 after undo, 40.00 after rematch | EXCH/0001 (+40), EXCH/0002 its reversal, EXCH/0003 (+40): net 40.00, nothing deleted | PASS |
| 7 | 50 EUR on D1 + 50 EUR on D2 | gain 20.00 | first receipt no EXCH; second +20.00 | PASS |
| 8 | AED bank receives 440 AED for the EUR invoice | gain 40.00 | bank 101405 +440.00 AED; receivable −440.00 / −100 EUR; EXCH 40.00 | PASS |
| 9 | ledger | bank 440 = income 400 + gain 40; receivable 0; suspense 0 | `{'101407': 440.0, '400000': -400.0, '441000': -40.0, '121000': 0.0}` | PASS |

**Conclusion.** On this pin and in `edit` mode (the default) the engine books **no** exchange line in the bank entry
at all: the realised difference is Odoo's own exchange entry created by `reconcile()`, once, reversed (not deleted) on
undo. The case the issue describes (statement, journal and document in the same foreign currency at the same rate) produces
**no** exchange entry (scenario 1). The issue's reported path (`_compute_exchange_rate` adding an exchange counterpart
inside the bank entry) was not reproduced with these fixtures; it remains an upstream report this audit neither confirms
nor needs a workaround for. The browser suite repeats scenario 2 and 6 through the UI (gain 40.00; undo 0; rematch 40.00).

## 7. Security (Option A unchanged)

* Write = `account.group_account_user` (Owner and Accountant through their roles); CEO / readonly: no *New* /
  *Upload* on the card **and** `AccessError` on `reconcile_bank_line`, `unreconcile_bank_line`, `action_nc_bank_new`.
* The Owner is still not `base.group_system` (fixture prints it).
* No `sudo` on any financial write. OCA's own two `sudo` calls (duplicate lookup by import id, the journal's import
  source) are reads / a non-financial flag.
* Record rules: OCA's multi-company rule on its reconcile data; statement lines follow core's company rules.

## 8. Release matrix (all ten databases of the authorised scope; versions read on the shared server, read-only)

Target versions: `ncollection_core` 1.28.0, `ncollection_account_core` 1.13.0, `ncollection_account_reports` 1.14.0,
`ncollection_account_dashboard` 1.6.0, `ncollection_branding` 1.19.0, `ncollection_subscription` 1.19.0,
`ncollection_dashboard_export` 1.1.0 (**`-i`**, needs `spreadsheet_dashboard`), `ncollection_bank_reconciliation_sale` 1.0.0 (auto_install: appears by itself with `-i ncollection_bank_reconciliation` on a database that has `sale`; read-only check: `ncollection`, `e2eclienta`, `atlas`, `test15`, `e2eclientb` have it, the other five do not), `ncollection_bank_reconciliation`
1.1.0 (**`-i`**, needs `ncollection_account_core`; pulls the nine OCA modules). Today every database is at core 1.27.0,
account_core 1.12.0, reports 1.12.0, dashboard 1.3.0, branding 1.18.0, subscription 1.18.0.

| Database | `-u` (from → to) | `-i` | Arabic reload | Notes |
|---|---|---|---|---|
| `ncollection` | core, account_core, account_reports, account_dashboard, branding, subscription | dashboard_export, **bank_reconciliation** (+ auto-installed **bank_reconciliation_sale**, Sales is installed) | ncollection_* + account_reconcile_oca | 5 bank/cash journals incl. Misr Bank; 0 statement lines; rehearsed |
| `e2euae` (protected) | core, account_core, branding | dashboard_export, **bank_reconciliation** | same | 3 e-invoices identical after rehearsal |
| `e2eclienta` | core, account_core, account_reports, account_dashboard, branding | dashboard_export, **bank_reconciliation** (+ auto-installed **bank_reconciliation_sale**, Sales is installed) | same | same module set as `atlas` (rehearsed) |
| `atlas` | core, account_core, account_reports, account_dashboard, branding | dashboard_export, **bank_reconciliation** (+ auto-installed **bank_reconciliation_sale**, Sales is installed) | same | rehearsed |
| `test15` | as `atlas` | dashboard_export, **bank_reconciliation** (+ auto-installed **bank_reconciliation_sale**, Sales is installed) | same | — |
| `einvdev`, `einvfull` | core, account_core, branding | dashboard_export, **bank_reconciliation** | same | as `e2euae` |
| `saastest` (platform) | core, branding, subscription | dashboard_export | ncollection_* | **no bank module** (no tenant accounting) |
| `e2eclientb` | core, branding | dashboard_export | ncollection_* | **no bank module** (no `ncollection_account_core`) |
| `e2eadmin` | core, branding, subscription | — (no `spreadsheet_dashboard`) | ncollection_* | **no bank module** (no Accounting) |

Prerequisites for every database: `make oca` with the two new repositories, the three `addons_path` entries, a verified
backup. Never `-u all`.

Zero statement lines everywhere: the empty *Transactions* list the user saw was simply the empty statement list; there
is no transaction data to migrate.

**Rehearsal** (scratch copies `pg_dump | pg_restore`, the source read-only; the previous sprint's `-u` list +
`-i ncollection_dashboard_export,ncollection_bank_reconciliation`, tree `feature/bank-reconciliation`):

| Copy of | Exit | 10/10 bank modules | Every bank/cash journal → workspace | Arabic card | 2nd upgrade | Accounting |
|---|---|---|---|---|---|---|
| `ncollection` | 0 | yes | 5/5 | yes | exit 0, no-op | residual 909.30 unchanged, overpaid backfill 0 |
| `e2euae` | 0 | yes | 1/1 | yes | exit 0, no-op | residual 210.00 unchanged; **the three e-invoices identical to live** (uuid, XML SHA-256, `validated` / `not_submitted`, write date) |
| `atlas` | 0 | yes | 1/1 | yes | exit 0, no-op | backfill 0 |

## 9. Release procedure (NOT executed — needs separate authorisation)

Per tenant database that has Accounting: `make oca` (two new repos), then
`-i ncollection_bank_reconciliation` (pulls the nine OCA modules; an existing database needs `-i`, `-u` installs
nothing), then `_update_translations(['ar_001'])` for `ncollection_bank_reconciliation` + `clear_all_caches`.
No data migration: existing statement lines are read as they are. `addons_path` gains two entries in
`config/odoo.conf`, `config/odoo.prod.conf`, `ci.yml` (guarded by `invariants.py` `rule_addons_path_matches_pins`).
Platform databases (`saastest`): not installed (no tenant accounting there).

## 10. Final enterprise-parity closure (sales orders, drag and drop, discounts, CODA, tour)

### 10a. Sales Order Matching (`ncollection_bank_reconciliation_sale`, `auto_install` with `sale`)

A sales order is **not** a receivable. Nothing in this module reconciles a bank transaction with an order, and a bank
line that names an order books no revenue. The workflow is Odoo's own:

1. On a transaction that has a customer, the workspace shows **Sales** (accountants only). The dialog lists that
   customer's **confirmed** orders of the transaction's company (cancelled / draft orders, another customer and another
   company are not offered; the record rules say the same): reference, date, customer, currency, total, already invoiced,
   un-invoiced balance, invoicing status, and an *Other currency* flag. An order named in the bank reference is ticked;
   with two orders and no reference, nothing is ticked (no guess).
2. **Create invoice** uses `sale.order._create_invoices(final=True)` (regular) or the native `sale.advance.payment.inv`
   (down payment: percentage / fixed). Posting is a separate tick-box; unticked, the invoice stays a draft. Ticked, it is
   posted with Odoo's normal posting and its receivable item is added to the transaction.
3. The user then validates the match in the workspace like any other (partial amounts, overpayment, exchange differences,
   undo are the engine's).
4. An order that is **already invoiced**: *Create invoice* is refused ("nothing left to invoice"), *Add their posted
   invoices* adds the posted, unpaid receivable items instead.

Guards (all server-side, as the user, **no sudo**): write access on the transaction; transaction not reconciled; the
orders re-read under `FOR UPDATE NOWAIT` (a concurrent creation is refused, never duplicated); an order of another
customer / company is refused even when the client forges a line; an order with a **draft invoice** is refused (a second
invoice would bill the same lines); a down payment needs the Sales user right the native wizard checks (an accountant
without it gets a clear message and nothing is created); a read-only user cannot open the wizard.

Native behaviour kept: several orders of one customer are invoiced on **one** invoice (Odoo's consolidated billing
default); the down-payment invoice is a normal invoice and the order stays *to invoice* for the balance.

Journal entries read back from the browser run (`uxbankweb`, disposable):

| Entry | Lines |
|---|---|
| invoice for S00001 (posted from the dialog) | Dr 121000 Account Receivable 1,000.00 / Cr 400000 Product Sales 1,000.00 → `paid` |
| bank entry BSLS/2026/00001 (validated in the workspace) | Dr 101407 bank 1,000.00 / Cr 121000 Account Receivable 1,000.00 — **no revenue line** |
| down-payment invoice for S00002 (30 %) | Dr Receivable 600.00 / Cr Product Sales 600.00 → `paid`; the order stays *to invoice* |
| bank entry BSLS/2026/00002 | Dr bank 600.00 / Cr Receivable 600.00 |

Tests: `TestSaleMatch` (17): order → invoice → match → reconcile with revenue checks; draft invoice; several orders;
ambiguous reference (nothing ticked); already invoiced (refused, then its invoice matched); duplicate creation refused;
down payment percentage and fixed (+ the remaining regular invoice of 700); down payment without the Sales right;
partially invoiced order; overpayment (customer credit −200); partial payment (residual 600); cancelled / draft / other
customer / other company / forged line; foreign-currency order flagged and invoiced in its currency; read-only user;
reconciled transaction; undo and retry. The concurrent-creation lock is the same `NOWAIT` helper proven with two real
sessions in `e2e/bank/concurrency.py`; it is not re-proven here.

### 10b. Drag and drop

`static/src/js/bank_drop.js` (a service) accepts a file dropped on a **bank journal card** or on the **matching
workspace of a journal** and calls `account.journal.action_nc_bank_drop(filename, content)`. That method re-checks
read / create access, the journal type (bank only) and the file type / size (`.csv .xlsx .xml .camt .qif .ofx .qfx .zip`,
10 MB, not empty), then opens **the same import wizard** with the file in place. A drop parses, previews and imports
**nothing**; Preview and Import are still required, and the wizard re-checks type and size when previewing and importing
(a renamed file is caught). One file at a time. No second importer, no new route (it is an ORM call, so CSRF and session
rules are Odoo's).

Verified: `TestDrop` (6: nothing imported, import without preview refused, the full drop → preview → import and the
duplicate re-drop, wrong type / size / empty / not base64, cash journal and read-only user refused, a swapped file name);
browser (`acceptance_sales.js`): card drop → wizard → Preview → Import (6 → 9 lines), three client refusals
(`.exe`, two files, 11 MB) with a notification and no wizard, workspace drop, Arabic RTL drop, CEO refused at the ORM.
Touch devices cannot drag; Upload remains.

### 10c. Early-payment discount coverage (§6b of the earlier list closed)

`TestDiscountCoverage` (14), AED company, 5 % VAT, 2 % term, expected figures computed by hand:

| Case | Hand computation | Booked |
|---|---|---|
| included, AED | 1,050 − 2 % of base (20) − 2 % of tax (1) | pays 1,029; discount 20.00, tax −1.00 |
| excluded, AED | 1,050 − 20 | pays 1,030; no tax line |
| **mixed**, AED | tax on the discounted base 5 % × 980 = 49 → invoice 1,049; − 20 | pays 1,029; discount 20.00; no tax correction |
| **EUR invoice, rate 4.00 → 4.40**, included | 105 EUR = 420.00 AED; pays 102.90 EUR = 452.76 AED; discount base 2 EUR = 8.00, tax 0.10 EUR = 0.40; ledger 411.60 | exchange gain **41.16** AED, once |
| EUR, same rate | — | no exchange entry; discount 8.00 |
| **EUR mixed**, rate change | 104.90 EUR; discount 2.00 EUR = 8.00 AED | gain 41.16 |
| window | discount date = invoice + 10 days | the last day is offered, the next day is refused and books nothing |
| partial payment (500) | not the discounted total | no discount; residual 550; a later 1,029 on the part-paid invoice is not discounted |
| wrong amounts (1,028.99 / 1,029.01 / 1,050) | — | never offered |
| credit note | not an eligible move type | no discount; refunded normally |
| vendor bill | pays 1,029 | discount **gain** −20.00, bank −1,029 |
| undo, then a second payment | — | residual back to 1,050; discount booked once (20.00) |
| two equal candidates | — | nothing proposed, nothing posted |

A bank line in another currency than the invoice never gets the discount.

### 10d. CODA — evidence assessment

* **Specification:** public. Febelfin publishes the CODA standard (versions 2.4, 2.6, 2.7 in English and French).
* **Odoo / OCA:** OCA `l10n-belgium` has `account_statement_import_coda` on **18.0 only** (the 19.0 branch exists but has
  no such module: HTTP 404 on its manifest; `bank-statement-import` 19.0 neither). It depends on the external `pycoda`.
* **Parser:** `pycoda` 1.1.0 (ACSONE, 2022-05) is **GPLv3+**, classified Python 2 / 3.5 and not in the Odoo image;
  adding a GPLv3+ dependency to a deliberately LGPL-3 module and a stock image is a licensing and architecture decision,
  not an implementation detail (CLAUDE.md: no new OCA / dependency without approval).
* **Fixtures:** no openly licensed real CODA sample was found that a parser could be verified against; writing the
  fixture ourselves from the same specification would test the parser against our own reading of it.
* **Decision:** not implemented; CODA files are refused with the "format not supported" message. **To close:** (1) a
  decision to depend on `pycoda` (GPLv3+) or to write a parser from the Febelfin spec, (2) real, shareable CODA files
  from the bank (anonymised) or from a Belgian customer, (3) the 19.0 port of the OCA module if the dependency is accepted.
  This is a format-specific limitation; the GCC / Egypt scope does not use CODA.

### 10e. External boundaries (not built, not faked)

* **Bank synchronisation:** needs a supported provider (Plaid, Ponto, Salt Edge, a UAE open-banking aggregator) with a
  contract, credentials and authorisation. The OCA `_online_*` provider modules are not installed; the card says
  "Upload a bank statement file, or add transactions with New" and shows no sync control. The integration point would be
  `account.online.provider`-style OCA modules that create `account.bank.statement.line` through the same import fingerprint.
* **Bill OCR / digitisation:** needs a document-recognition service (Odoo IAP is Enterprise; a third-party OCR API would
  need a contract). No upload-bill control is shown.

### 10f. OCA browser tour

`account_reconcile_oca` `TestAccountReconcileOCAJS.test_automation_oca` needs `websocket-client` and a browser, neither
in the image. It was run **in a throw-away image built on the side** (Google Chrome 157 from the vendor's `.deb` and
`websocket-client` 1.9.2 installed in a copy of `odoo:19`, image deleted afterwards; no package was installed in the shared
image, stack, volume or CI): **1 test, 0 failed, 0 errors, executed (~16 s), not skipped.** CI does not run OCA's own tests
and does not have these dependencies, so the skip is not added to `expected_skips.txt`; it is a local, isolated result.

## 11. Licensing inventory (not a legal opinion; qualified review required before an external commercial rollout)

| Module | Declared licence | Pin | Modified by us? | Network-user obligation |
|---|---|---|---|---|
| `account_reconcile_oca` | AGPL-3 | `OCA/account-reconcile@e3d99f3d` | **No source modified** (extended through `_inherit` in our module) | AGPL §13: users interacting over a network must be offered the Corresponding Source of the version they use |
| `account_statement_import_file_reconcile_oca`, `_camt`, `_sheet_file`, `_sheet_file_xlsx`, `_qif` | AGPL-3 | `OCA/bank-statement-import@aa647ee5` (and account-reconcile for the first) | No | same |
| `account_statement_base`, `account_statement_import_base`, `_file` | LGPL-3 | same | No | LGPL: replaceable, notices kept |
| `ncollection_bank_reconciliation`, `ncollection_bank_reconciliation_sale` | LGPL-3 (declared by us) | this repository | — | they `_inherit` AGPL modules and ship together with them in a hosted service; whether our modules then have to be offered under AGPL §13 is **unresolved** |

Nothing was copied from Odoo Enterprise (the Enterprise documentation was the functional specification only). The
`i18n_oca/*` files are our own translations of OCA's messages and are shipped next to the OCA modules' own strings.
**Open questions for counsel:** AGPL §13 for the hosted SaaS and for the derivative `_inherit` modules; whether LGPL
declaration of our modules is sufficient; the source-offer mechanism for tenants. The licence metadata was **not**
changed.

Security additions (§10): the Sales wizard and the dropped-file action are write-gated at the server (`check_access('write')` /
`'create'`, accountant group on the wizard ACL); the read-only role (CEO) is refused at the ORM for both (browser-verified);
the Owner keeps Option A (no `base.group_system`); invoice creation runs as the user and a down payment needs the Sales right;
no `sudo` anywhere in either module.
