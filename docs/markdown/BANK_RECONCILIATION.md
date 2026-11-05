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
| D9 | translations | `account_reconcile_oca` ships 0 / 128 Arabic terms | 189 OCA view / field / menu / JS terms translated in our catalogue; OCA **Python** messages stay English (Odoo looks them up under their own module) |

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

| Enterprise behaviour | Community before | Now | Evidence |
|---|---|---|---|
| Journal card *Transactions* opens Bank Matching | empty statement list | **YES** OCA workspace, journal-filtered | browser: card + workspace, 5 = 5 lines |
| *Not Matched* filter / all lines incl. reconciled | — | **YES** *To Reconcile* (4) / *Transactions* (5) | browser |
| Split view: list left, matching right, global balance | — | **YES** (OCA layout, `Global Balance` 3,575.00 AED = ledger) | screenshot |
| Manual transaction (*New*: date, label, partner, amount) | statement form only | **YES** card *New* | browser + test |
| Upload CAMT.053 / CSV / XLSX / QIF | none | **YES** with preview | tests 2, 12, CAMT, CSV |
| Upload OFX / CODA | — | **NO** — no 19.0 OCA module | limitation |
| Drag-and-drop a file on the card | — | **NO** — *Upload* link instead | limitation |
| Match invoices, bills, payments, outstanding items | — | **YES** | tests 3-8, browser |
| Partial / one-to-many / many-to-one / overpayment / underpayment | — | **YES** | tests 4-7 |
| Set Account (write-off), bank fees, interest | — | **YES** | tests 1, 9 |
| Receivable / Payable (unallocated) | — | **YES** | tests 7, 8 |
| Exchange difference | — | **YES** booked once | test 10 |
| Internal transfer via transfer account | — | **YES** no revenue/expense | test 11 |
| Undo (unreconcile) | — | **YES** audited | test 13, browser |
| Reconciliation models: manual buttons, automatic, journals / partners / amount / label conditions, counterpart lines (fixed, %, regex) | native models, no UI to apply | **YES** (OCA applies native `account.reconcile.model`) | test 9, OCA suite |
| Automatic matching never posts an ambiguous match | — | **YES** | `test_automatic_matching_never_posts_an_ambiguous_match` |
| *To check* flag | — | **YES** (OCA) | OCA suite |
| Match against **Sales Orders**, **Batch payments**, **Upload bills (digitisation)** | — | **NO** — Enterprise / IAP features | limitation |
| *Find Duplicate Transactions* (after the fact) | — | **PARTIAL** — duplicates are refused at import; no after-the-fact finder | limitation |
| Bank synchronisation (Ponto, Plaid…) | — | **NO** — needs a provider contract; not offered | limitation |
| Early-payment discount suggestion | — | **NOT VERIFIED** | limitation |

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

## 7. Security (Option A unchanged)

* Write = `account.group_account_user` (Owner and Accountant through their roles); CEO / readonly: no *New* /
  *Upload* on the card **and** `AccessError` on `reconcile_bank_line`, `unreconcile_bank_line`, `action_nc_bank_new`.
* The Owner is still not `base.group_system` (fixture prints it).
* No `sudo` on any financial write. OCA's own two `sudo` calls (duplicate lookup by import id, the journal's import
  source) are reads / a non-financial flag.
* Record rules: OCA's multi-company rule on its reconcile data; statement lines follow core's company rules.

## 8. Release (NOT executed — needs separate authorisation)

Per tenant database that has Accounting: `make oca` (two new repos), then
`-i ncollection_bank_reconciliation` (pulls the nine OCA modules; an existing database needs `-i`, `-u` installs
nothing), then `_update_translations(['ar_001'])` for `ncollection_bank_reconciliation` + `clear_all_caches`.
No data migration: existing statement lines are read as they are. `addons_path` gains two entries in
`config/odoo.conf`, `config/odoo.prod.conf`, `ci.yml` (guarded by `invariants.py` `rule_addons_path_matches_pins`).
Platform databases (`saastest`): not installed (no tenant accounting there).
