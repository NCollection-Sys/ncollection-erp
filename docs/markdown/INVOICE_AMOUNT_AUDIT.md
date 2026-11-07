# Invoice amount audit (#81 / P8-T05 acceptance criterion)

**Criterion (DELIVERABLE_1_SYSTEM_DESIGN.md, P8-T05):** "changing an invoice amount produces an audit entry with old/new values, user, IP, and timestamp."

**Status:** implemented and tested in `ncollection_account_audit` 19.0.1.0.0. **Not installed on any database, in no plan, never `auto_install`.** Installing it is a
separate, explicit decision (section 6). The earlier statement that the task was "done" was wrong for this criterion; `ncollection_audit` (OCA `auditlog` infrastructure) is unchanged.

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
| Access | read-only ACL for Billing/Accounting Manager, Owner and CEO; create, write, unlink for nobody; rows are created with `sudo` by the service; record rule `company_id in company_ids` |
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
* Install on a database: `-i ncollection_account_audit` (it creates one table and one menu: Accounting > Reporting > Invoice Amount Audit). Rollback: uninstall the module (drops the table; the rows go with it),
  or restore the database backup.
* Because it wraps every invoice and line write, install it on a database only after the owner approves; it adds two small reads of the invoice's amounts per outermost write.

## 6. Release decision

It is not in the three-database matrix. The shared upgrade is unaffected whether or not the owner later installs it. Until it is installed on a database, **that database has no
invoice-amount audit trail**; the requirement is met by the code, not by any deployed database.
