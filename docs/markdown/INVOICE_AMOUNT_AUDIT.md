# Invoice amount audit (#81 / P8-T05 acceptance criterion)

**Criterion (DELIVERABLE_1_SYSTEM_DESIGN.md, P8-T05):** "changing an invoice amount produces an audit entry with old/new values, user, IP, and timestamp."

**Status:** implemented and tested in `ncollection_account_audit` 19.0.1.0.0. **In the release matrix as pass 2 for `ncollection`, `awtar` and `ncollection_demo`; in no plan and never `auto_install`; not installed on the shared databases until the controlled upgrade runs.**
The earlier statement that the task was "done" was wrong for this criterion; `ncollection_audit` (OCA `auditlog` infrastructure) is unchanged.

## 1. Why not OCA `auditlog` (OCA-first check, measured)

* A rule on `account.move` logs nothing for a line edit: `amount_total` is a stored computed field, recomputed without passing through the `write()` that `auditlog` patches
  (probe: 100 to 250 with zero move audit rows).
* A rule on `account.move.line` overflows the recursion limit (79 nested `write_full` frames, #431).
* Fixing either means patching `auditlog` or Odoo core, which the project forbids. So the criterion needs a purpose-built component; `ncollection_audit` keeps what `auditlog` does well.

## 2. What it does

One row of `ncollection.invoice.audit` is written per **net change** of an invoice's amounts caused by one **outermost** ORM operation (`create`, `write`, `unlink` on
`account.move`; `create`, `write`, `unlink` on `account.move.line`), by comparing `amount_untaxed`, `amount_tax`, `amount_total` (the booked figures) and the sum of the
product lines' `price_total` before and after.

| Property | How |
|---|---|
| Old / new values | `old_*` / `new_*` for untaxed, tax, total and lines total, plus the currency |
| Actor | `user_id` plus the login copied as text (survives archiving) |
| IP | the HTTP request's `remote_addr`; empty for cron, shell and tests (never invented) |
| Timestamp | `logged_at` |
| Company, invoice identity | `company_id`; `res_id`, number, type, state, partner name copied as values; `move_id` is a link that becomes empty if a draft is deleted |
| No duplicates | an operation nested in another audited one is covered by the outer one: one user action is one row |
| No misleading rows | no row when the amounts are unchanged (a note, a label, posting, a payment); a failed operation raises before anything is recorded and rolls back with its transaction |
| Events | `created`, `amount_changed`, `deleted` |
| Accounting protections | untouched: the audited operation runs exactly as before; the audit only reads amounts (as `sudo`) and writes its own table. Nothing weakens the posted-entry lock, hash, lock dates or tax locks |
| Access | read-only ACL for Accountants (Odoo Accountant group and the NCollection Accountant role), Billing/Accounting Manager, Owner and CEO; create, write, unlink for nobody; rows are created with `sudo` by the service; record rule `company_id in company_ids` |
| Integrity | `write` and `unlink` raise, even for `sudo`; a SHA-256 digest of each row's content lets `nc_verify()` find a row altered with SQL |
| Logs | the module never writes to the server log (a test asserts no log record at any level) |
| Tenant isolation | the table lives in the tenant's database; nothing crosses databases |

A measured core behaviour the audit makes visible: on Odoo 19 **writing `price_unit` on a posted invoice line is not refused and does not re-price the booked entry**. The
booked total stays and the lines stop agreeing with it. The audit records exactly that (state `posted`, booked amounts equal, lines total different). Protected fields
(`balance`, `debit`, `credit`, ...) are still refused by core and leave no row.

## 3. What it does NOT cover (stated, not hidden)

* Changes made by raw SQL.
* A recompute that no write on a move or line caused (for example a tax-rate or currency-rate change that re-prices a draft later); the next write on the invoice records the net difference.
* Reads, and the invoice history of other models (`res.partner`, `res.users`, `sale.order`): out of this criterion; `ncollection_audit` and #428/#429 stay as documented.
* Retention: rows are kept; there is no purge job.
* Draft invoices produce one row per save that changes an amount, which is the faithful record of an edit session.

## 4. Tests (`make test m=ncollection_account_audit`, 20 tests)

Permitted: creation, a line price change (old/new/user/timestamp/company), IP with and without a request, line commands through the move (one row), several lines in one write
(one row), add / remove a line, delete a draft, change after reset to draft, a different actor. Rejected: a protected change on a posted invoice, a user without rights (no row).
Integrity: sudo cannot edit or delete, SQL tampering is detected, who can read, another company cannot see rows, nothing logged. Regression: notes / renames / no-op / posting record
nothing, journal entries are not audited, posting + payment + credit note behave as without the module.

## 5. Install, rollback, upgrade impact

* Depends on `account` and `ncollection_core`; no OCA dependency, no change to `repos.yml`, no new group.
* Install on a database: `-i ncollection_account_audit` (it creates one table and one menu: Accounting > Invoice Amount Audit). Rollback: uninstall the module (drops the table; the rows go with it),
  or restore the database backup.
* Because it wraps every invoice and line write, install it on a database only after the owner approves; it adds two small reads of the invoice's amounts per outermost write.

## 6. Release decision
It is part of the three-database matrix (`RELEASE_LOCAL_UPGRADE_PLAN.md` section 2, pass 2), installed by the controlled procedure after pass 1 of each database. Rehearsed on disposable copies of all three: installed, idempotent, no schema drift, the UI (English and Arabic) lists and shows the entries, unauthorized users are refused.

## 7. Posted-invoice line edits: what stock Odoo does (measured, release closure)

Probe on a database with ONLY `account` installed (no NCollection module), a posted 100.00 invoice, as superuser:

| Step | line `price_unit` / `price_subtotal` | booked `amount_total` / untaxed | journal items (debit / credit) | residual |
|---|---|---|---|---|
| posted | 100 / 100 | 100 / 100 | product 0 / 100, receivable 100 / 0 | 100 |
| `price_unit = 999` | 999 / 999 | 100 / 100 | unchanged | 100 |
| `quantity = 5` | 999 / 4995 | 100 / 100 | unchanged | 100 |

So this is **standard Odoo 19 behaviour**, not a custom-module issue and not a test artifact. Accounting integrity of the books is intact (journal items, taxes, residual, reconciliation are untouched);
what diverges is the line's own price figures against the booked entry. The audit is accurate here: it compares the booked amounts and the lines total separately, so the entry shows booked equal,
lines different. Editing journal-item fields (`debit`, `credit`, `balance`) of a posted entry is a different matter: core refuses it by the balance and lock checks (tested: refused, no row).
No change to accounting core is made. **Policy decision for the owner (not release-blocking):** whether to add a constraint refusing price / quantity / discount / tax edits on posted lines.
**Owner decision (2026-11-07): no new posted-invoice editing restriction in this release.** Standard Odoo behaviour is preserved and posted-invoice accounting is not silently changed. The possible discrepancy
between a posted line's own figures (`price_unit`, `quantity`, subtotal) and the booked accounting values is recorded as a **separate, future accounting-policy decision** (whether to refuse price / quantity /
discount / tax edits on posted lines). Until then the audit keeps recording the difference accurately: booked amounts and lines total are stored side by side in every entry.

## 8. Who can read the trail (owner decision, 2026-11-07)
Accountants (Odoo's `account.group_account_user` and the NCollection `group_role_accountant`) have READ-ONLY access, in addition to Billing / Accounting Manager, Owner and CEO. Company isolation is the same record rule;
nobody can create, modify or delete an entry (the model refuses write / unlink, the ACL grants no create), and `nc_verify()` is unaffected. Three tests cover it (read, refusal, digest, company, menu).
