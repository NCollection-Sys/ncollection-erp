# Bank & Cash Journal Operations (`ncollection_journal_operations`)

The Accounting dashboard's bank and cash cards as a financial operations centre: Current Balance, Pending Balance,
complete Transactions, and the four operations **Payment · Receipt · Internal Transfer · Reconcile**, plus **Mark as
Paid** on an in-process payment or receipt. Every bank / cash journal gets it from its `type`; nothing is keyed on a
journal, account, method, company or currency.

No accounting engine is added. Every posting is Odoo's own: `account.payment` (`action_post`), `account.bank.statement.line`
(created with Odoo's `counterpart_account_id`) and `account.move.line.reconcile()`. No payment state is written by
this module; Odoo recomputes it from the residual (`_compute_state` depends on `move_id.line_ids.amount_residual`).

## 1. Odoo 19 Community facts this relies on (read in the installed source)

| Fact | Where |
|---|---|
| A payment's move has a *liquidity* line on `outstanding_account_id` (= the method line's `payment_account_id`) and a counterpart on `destination_account_id`. | `account_payment._prepare_move_liquidity_lines / _counterpart_lines` |
| `state` = `in_process` while the liquidity lines still have a residual on a reconcilable account; `paid` when it is zero or the account is not reconcilable, or when every reconciled invoice is paid. `action_post` sets `paid` directly when the outstanding account is `asset_cash`. | `_compute_state`, `action_post` |
| A method whose payment account IS the journal's default (liquidity) account is a *direct* payment: `is_matched` is true immediately. | `_compute_reconciliation_status` |
| Community forces an outstanding account (`account_journal_payment_debit/credit_account_id`, else `transfer_account_id`) on every payment so a journal entry always exists. | `create` |
| A counterpart line on `company.transfer_account_id` is a valid payment counterpart (internal transfers); `paired_internal_transfer_payment_id` still exists. The old `is_internal_transfer` flag does not. | `_seek_for_lines`, field list |
| A statement line can be created with `counterpart_account_id` instead of the suspense account. | `account_bank_statement_line.create` |
| Odoo's own card *Balance* is `current_statement_balance + direct payments`, so a journal used only with outstanding-account payments and never reconciled shows no balance at all. | `_fill_bank_cash_dashboard_data` |

## 2. Definitions

**Current Balance** (الرصيد الحالي) = the posted ledger balance of the journal's `default_account_id`:
`SUM(balance)` of posted `account.move.line` on that account, company-restricted; in the journal currency
(`SUM(amount_currency)` of its lines in that currency) when the journal has a foreign currency. Draft entries never
count. It needs no reconciliation and no statement. It is the BOOK balance, not a bank-confirmed or a counted cash
balance (the card says "Ledger"); the bank-confirmed figure stays Odoo's "Last Statement".

**Pending Balance** (الرصيد المعلق) = for posted payments of the journal in state `in_process`, the remaining
residual of their liquidity lines that are on an account OTHER than the journal's default account (the outstanding
receipts / payments accounts, read from the posted line, never from the current method configuration). Incoming
residuals are positive (debit on Outstanding Receipts), outgoing negative. The card shows the signed net and, in a
tooltip and in the pending list, the incoming and outgoing totals separately. A partial settlement shows only what
remains. When the lines are in several currencies the card shows the company-currency figure marked "≈" and the list
shows each currency.

Pending list = exactly the payments behind that number (same domain, `nc_pending_amount != 0`).

**Transactions** = `ncollection.journal.transaction` (SQL view, read only):
1. every posted payment of the journal: amount, pending, status, method, partner or account, type (Payment / Receipt /
   Internal Transfer / Account);
2. every posted statement line: a plain **Bank Transaction**, or a **Bank Confirmation** when it settles a payment of
   the same journal (Mark as Paid, or matched with the payment's outstanding line). A confirmation is linked to its
   payment and has no Incoming / Outgoing amount, so the money of one operation is never counted twice;
3. every other posted line on the journal's default account with no payment and no statement line (opening balances,
   manual entries).

**Running balance — exact, by posted lines.** Every posted line on the journal's account belongs to exactly one row,
and `liquidity_balance` is the sum of that row's posted lines on the account (company currency), never a nominal
payment amount. A receipt of 100 matched by a bank line of 95 counts 95 on the bank line's date, the remaining 5 on
the date it is settled; a payment settled a week later reaches the balance that week. The running balance is the
window sum in (date, id) order, so the row at any cutoff equals the posted ledger of the account at that date
(test `test_partial_and_different_amounts_follow_the_posted_lines_at_every_cutoff`).

## 3. Operations

| Operation | Records | Entry (example: 100 AED, Bank Misr, outstanding method) |
|---|---|---|
| **Receipt / Customer** | `account.payment` inbound, partner_type customer | Dr Outstanding Receipts 100 / Cr Receivable (partner) 100 |
| **Receipt / Supplier** | inbound, supplier (refund) | Dr Outstanding Receipts / Cr Payable |
| **Receipt / Account** | inbound, no partner, `destination_account_id` = chosen account | Dr Outstanding Receipts / Cr chosen account (e.g. Other Income) |
| **Payment / Supplier** | outbound, supplier | Dr Payable / Cr Outstanding Payments |
| **Payment / Customer** | outbound, customer (refund) | Dr Receivable / Cr Outstanding Payments |
| **Payment / Account** | outbound, no partner, chosen account | Dr Bank Fees 100 / Cr Outstanding Payments 100 |
| **Internal Transfer** | `ncollection.internal.transfer` + 2 payments | source: Dr Transfer account / Cr Outstanding Payments (source); destination: Dr Outstanding Receipts (dest) / Cr Transfer account; the two transfer lines are reconciled together. Each side then completes on its own bank (statement or Mark as Paid). |
| **Mark as Paid** | 1 statement line + 1 reconciliation | Receipt: Dr Bank Misr 101012 100 / Cr Outstanding Receipts 101003 100, reconciled with the payment's Dr Outstanding Receipts line. Payment: Dr Outstanding Payments / Cr bank. |

With a direct method (payment account = default account) the payment is already `paid` on posting and never pending.

**Account type**: the Payee is an account of the active company that is not receivable / payable (use Customer /
Supplier), not a liquidity account (use Internal Transfer), not off-balance, not the company transfer account, and not
one of this journal's own outstanding accounts (self-offsetting). No placeholder partner is created. Taxes are NOT
computed on a direct account operation (record a taxable expense as a bill); an analytic distribution may be set and
is written on the counterpart line.

**Journal lock**: the card writes `nc_source_journal_id`; a constraint makes `journal_id == nc_source_journal_id` and
the direction (`payment` = outbound, `receipt` = inbound) hold on create AND write, and both fields are immutable, so a
crafted context or RPC cannot move an operation to another journal or reverse it. The journal is not on the form.

## 4. Mark as Paid

**What it is, and is not.** Mark as Paid records the USER's confirmation that the money reached (or left) the account.
Nothing is checked with the bank. The dialog says so in a warning, the button reads "Confirm: Mark as Paid", and the
chatter note says "confirmed by the user, not verified with the bank". A later bank file is what confirms it
(`nc_bank_confirmed`).

Eligible iff: posted move, state `in_process` (or Community's `paid` with the money still on the outstanding
account), company in the user's companies, and at least one liquidity line on a reconcilable account other than the
default account with a non-zero residual. The user needs `account.group_account_user` and create rights on statement
lines (checked, no sudo).

Steps, in one transaction: `SELECT … FOR UPDATE NOWAIT` on the payment (a second click / user gets "being completed");
re-read and re-check eligibility; refuse if an UNRECONCILED statement line of the same journal has the same amount
(the bank already reported it: match it in Reconcile instead); create a statement line in the journal for the residual
(journal currency; foreign currency and amount when the payment's differs) with `counterpart_account_id` = the
outstanding account of the posted line; reconcile its counterpart line with the payment's liquidity line(s) through
`reconcile()` (exchange differences by Odoo); link it (`nc_settlement_payment_id`, unique) and post a chatter note.
Odoo then recomputes the payment to `paid`; invoice / bill reconciliation is untouched.

**Two sessions at once** (measured on a private copy with two real, committed database sessions,
`scratchpad/jo_conc.py`): B arriving while A held its uncommitted lock, and A and B starting at the same instant —
both times exactly one settlement, the other session refused ("being completed by another user"), +1 statement line
and +1 journal entry only, payment `paid` and matched. A third attempt after the commit is refused because nothing is
left on the outstanding account, and `unique_import_id nc-settle-<payment>` is unique in the database.

**A statement imported later**: in `_complete_stmts_vals`, a file line with the same journal and exact amount, dated
within 7 days of a Mark-as-Paid settlement, AND linked to it by identity (same partner, or the payment's number /
memo in the label or reference) takes the settlement's `unique_import_id`, so the OCA importer skips it; the
settlement is marked bank-confirmed and keeps the bank's own id, so re-importing the same file is skipped too. Amount
and date alone never match. Measured: the same money imported once (skipped), an unrelated line of the same amount
imported, the same file again added nothing.

## 5. Security

Card figures are read with the user's rights (`_read_group`, company domain of `env.companies`). Buttons carry
`groups="account.group_account_user"` and every action re-checks on the server. No `sudo()`. The transaction view
has an ACL for accounting read-only and a multi-company record rule.

## 6. Terminology

English: Vendor → Supplier on the accounting / purchase menus, actions and form labels changed by records and view
inheritance (technical names untouched); Accounting > Customers > Payments → Receipts. Arabic: already "المورد"; the
new terms are in `i18n/ar.po`. Python-coded messages of Odoo itself cannot be re-worded in English without changing
core (Odoo never translates `en_US`); see limitations.

## Owner review 2026-11

* **Incoming Payment Method.** An internal transfer has an Outgoing Payment Method (From Journal) and an Incoming
  Payment Method (To Journal, `destination_payment_method_line_id`): the To Journal's incoming methods only, the first in
  journal order by default, replaced when the To Journal changes, validated server-side, and USED by the receiving
  payment. Posted transfers recorded before this release take the method their receiving payment really used
  (migration). Status follows both payments' real state (Paid only when both are paid).
* **Not a customer receipt / supplier payment.** Odoo's Customer Receipts and Supplier Payments actions get the domain
  `nc_transfer_id = False` (they had none). A transfer leg is identified by that authoritative link only; a receipt or
  payment without partner (an Account operation) stays listed. Transfers stay on both journals' Transactions (the row
  opens the transfer) and in Internal Transfers.
* **Balance panels** use the operation buttons' frame (thin neutral border, button radius, no fill, no coloured bar).
