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
| Import CODA (Belgium) | yes | none on 19.0 (Community / OCA `l10n-belgium`) | refused with a clear "format not supported" | `test_ofx_malformed_or_foreign_files_are_refused_whole` | **NOT IMPLEMENTED**: no 19.0 module, no openly licensed reference sample to verify a parser against; no market need in GCC/Egypt |
| Drag-and-drop file on the card / workspace | yes | no | *Upload* button | — | **NOT IMPLEMENTED** (UX convenience; Upload covers the function) |
| Preview before import | test-before-import for CSV | no | every format, exact file (SHA-256) | tests + browser | — **VERIFIED** |
| Duplicate prevention at import | yes | id-based only | bank id or fingerprint | `test_12`, `test_ofx_*`, `test_camt_*` | — **VERIFIED** |
| *Find Duplicate Transactions* (after the fact) | yes | no | Duplicate review: explained candidates, *Not a duplicate*, never deletes | `test_duplicate_review_*`, browser | deletion of a real duplicate stays the standard Odoo action — **VERIFIED** |
| Match invoices / bills / payments / outstanding items | yes | yes | + locking, no double use | tests 3-8, browser | — **VERIFIED** |
| Partial / many-to-one / one-to-many | yes | yes | — | tests 4-6 | — **VERIFIED** |
| Overpayment → customer credit; vendor advance | yes | partial (stuck Validate) | remainder on the partner's account is bookable | tests 7-8 | — **VERIFIED** |
| Set Account / write-off / bank fees / interest | yes | yes | — | tests 1, 9; browser (Bank Fees model) | — **VERIFIED** |
| Internal transfer | Internal Transfers model | yes | — | test 11; browser (both legs, transfer account nets 0) | — **VERIFIED** |
| Exchange difference | yes | yes (Odoo exchange entries) | independent audit, 9 scenarios | `TestForeignCurrencyAudit`; browser | — **VERIFIED** |
| Early-payment discount | discounted match suggested | **none** (and the engine never proposes a discounted amount) | discounted-amount PROPOSAL (one candidate or none) + *Apply early payment discount* with Odoo's own discount computation (base / tax / exchange), only inside the window, only the exact discounted total | `test_early_discount_*` (included, excluded, refused, first opening, ambiguous); browser (VAT −1.00, paid in full) | `mixed` computation and multi-currency discounts not separately tested — **VERIFIED for included / excluded, same currency** |
| Batch payments | `account_batch_payment` | none on 19.0 | `ncollection.bank.payment.batch` + *Match a batch* | `test_batch_*`; browser | no bank-file (SEPA/ISO) generation for batches — **VERIFIED for matching** |
| Sales orders in the matching | match orders → invoice | no | **NOT IMPLEMENTED**: invoice first, then match; the SO reference in a label is matched by label like any reference | — | **NOT IMPLEMENTED** |
| Upload bills for digitisation (OCR) | IAP service | no | — | — | **EXTERNAL** (Odoo IAP / OCR provider) |
| Bank synchronisation (Ponto, Plaid, …) | Odoo online sync | `_online_*` providers (not installed) | not offered; card text says so | — | **EXTERNAL** (provider contract + credentials) |
| Reconciliation models: manual / automatic, journals, partners, amount, label (contains / regex), counterparts fixed / % / regex | yes | yes (native models) | — | test 9, OCA suite, ambiguous-match test | — **VERIFIED** |
| Never auto-post an ambiguous match | implicit | yes | — | `test_automatic_matching_never_posts_an_ambiguous_match` | — **VERIFIED** |
| *To check* | yes | yes | — | OCA suite | — **VERIFIED (OCA)** |
| Undo | yes | yes (stale data bug) | fixed + audited | test 13, FX audit, browser | — **VERIFIED** |
| Concurrent matching | n/a | no | `NOWAIT` locks | two real sessions | — **VERIFIED** |
| Arabic UI | yes | 0 % | 310 interface + 56 Python messages | browser AR, `test_oca_python_messages_have_arabic` | the two CAMT schema codes stay as identifiers |

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
`ncollection_dashboard_export` 1.1.0 (**`-i`**, needs `spreadsheet_dashboard`), `ncollection_bank_reconciliation`
1.1.0 (**`-i`**, needs `ncollection_account_core`; pulls the nine OCA modules). Today every database is at core 1.27.0,
account_core 1.12.0, reports 1.12.0, dashboard 1.3.0, branding 1.18.0, subscription 1.18.0.

| Database | `-u` (from → to) | `-i` | Arabic reload | Notes |
|---|---|---|---|---|
| `ncollection` | core, account_core, account_reports, account_dashboard, branding, subscription | dashboard_export, **bank_reconciliation** | ncollection_* + account_reconcile_oca | 5 bank/cash journals incl. Misr Bank; 0 statement lines; rehearsed |
| `e2euae` (protected) | core, account_core, branding | dashboard_export, **bank_reconciliation** | same | 3 e-invoices identical after rehearsal |
| `e2eclienta` | core, account_core, account_reports, account_dashboard, branding | dashboard_export, **bank_reconciliation** | same | same module set as `atlas` (rehearsed) |
| `atlas` | core, account_core, account_reports, account_dashboard, branding | dashboard_export, **bank_reconciliation** | same | rehearsed |
| `test15` | as `atlas` | dashboard_export, **bank_reconciliation** | same | — |
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
