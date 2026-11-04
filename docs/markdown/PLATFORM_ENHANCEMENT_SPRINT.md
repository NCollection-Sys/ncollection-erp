# Platform enhancement sprint — what changed, what it means, how to release it

Branch `feature/platform-enhancement-sprint` (base `5decb87`). Nothing here is deployed; the shared runtime is untouched.

## 1. Settlement (Paid Amount, Balance Due, Overpaid)

Five different things are kept apart, because mixing them is how a report lies:

| Thing | Where it lives | Changed by this sprint |
|---|---|---|
| Native amount due | `account.move.amount_residual` | never |
| Payments reconciled to the document | `account.partial.reconcile` | never |
| **Invoice-attributable excess** | `nc_overpaid_amount` (stored) | new |
| Unallocated customer credit | the open (unreconciled) line of the payment | never; shown on the payment (`nc_is_overpaid`) and on the dashboard (Customer Credit) |
| Refund / credit-note balance | the credit note's own residual | never |

**A negative Balance Due is shown only with evidence.** An excess is attributed to an invoice only when (1) the payment was
**recorded against that invoice** with Odoo's Register Payment (the wizard stamps `account.payment.nc_registered_move_ids`),
(2) the payment line settled **nothing else**, and (3) the document's own residual is zero. An advance that a user later assigns
to an invoice with the outstanding-credits widget, a bank import, a payment of an earlier release, a payment spread over several
invoices, and a credit-note remainder are all **customer credit**, not the invoice's excess: the invoice reads Paid in Full /
Balance Due 0 and the credit is shown once, on the payment. `Paid Amount + Balance Due = total` always.

Lifecycle, measured on Odoo 19 (`TestSettlementExcess`): **resetting a payment to draft does not remove its reconciliation** — the
invoice stays paid and the excess stays until the reconciliation is removed (`remove_move_reconcile`), at which point the stored
excess recomputes to 0 and the native residual returns. Reversal by credit note makes the invoice `reversed`: no status, no excess.

Only `nc_overpaid_amount` is stored; Paid Amount, Balance Due and Settlement Status are computed and **sortable in SQL**
(`_order_field_to_sql`), so ordering is the database's, over the whole result set, with record rules applied.

Taxable / Non-taxable is decided by the **tax group** of the lines (matched on the stable external-id suffix, never the translated
name): standard and zero-rated are taxable; exempt, out of scope and untaxed are not. A zero VAT amount alone decides nothing.

## 2. Release requirements (not executed — needs separate authorisation)

* Modules: `ncollection_core`, `ncollection_account_core`, `ncollection_account_dashboard` (and the manifests bumped for them).
* New stored column `account_move.nc_overpaid_amount` and the table `nc_payment_registered_move_rel` are created by the module
  upgrade. **Backfill:** the column computes on upgrade from existing reconciliations; because the stamp did not exist before,
  every historical payment has no evidence, so **every existing document backfills to 0** — no historical overpayment is invented.
  Historical remainders appear as customer credit.
* New Arabic terms are NOT loaded by a targeted `-u`: run `mods._update_translations(['ar_001'])` for the touched modules, then
  `env.registry.clear_all_caches(); env.registry.signal_changes()` (see the 2026-11-04 release notes).
* nginx (separate approval): the committed `ncollection.dev.conf` / `ncollection.prod.conf` re-resolve the `odoo` upstream
  (`resolver 127.0.0.11 valid=10s ipv6=off;` + `zone` + `server … resolve;`, nginx >= 1.27.3; the image is 1.27.5). Deploy =
  `nginx -t` then `nginx -s reload`. Proof: `scripts/dev/nginx_upstream_reresolve_test.sh` (private network; the old config FAILs,
  the new one PASSes).
* Rollback: restore the pre-release dump of each database (the release procedure's backup volume) and the previous source tree.

## 3. Dashboard accuracy release (DASH-02) — database × module × version × migration

Evidence for every KPI: `DASHBOARD_ACCEPTANCE_MATRIX.md`. This section is only what an operator must do. **Nothing here was executed
on a shared database**; every row below was rehearsed on a scratch copy (`pg_dump | pg_restore`, source read-only) or a disposable fixture.

| Database (role) | Module | Current | Target | Migration |
|---|---|---|---|---|
| `saastest` (platform) | `ncollection_core` | 19.0.1.27.0 | 19.0.1.28.0 | `-u`: new service model, wizard override, ACL rows, translations; no schema change on this DB |
| | `ncollection_subscription` | 19.0.1.18.0 | 19.0.1.19.0 | `-u`: code only (MRR in one currency, "expiring soon") |
| | `ncollection_dashboard_export` | — | 19.0.1.0.0 | **`-i`** (an `auto_install` module is NOT installed by `-u` on an existing database) |
| `ncollection` (accounting tenant and platform modules) | `ncollection_core` | 19.0.1.27.0 | 19.0.1.28.0 | `-u` |
| | `ncollection_account_core` | 19.0.1.12.0 | 19.0.1.13.0 | `-u`: new stored `account_move.nc_overpaid_amount` + table `nc_payment_registered_move_rel`; **backfill 0** |
| | `ncollection_account_reports` | 19.0.1.12.0 | 19.0.1.13.0 | `-u`: new abstract model (open items), Financial Summary figures |
| | `ncollection_account_dashboard` | 19.0.1.3.0 | 19.0.1.4.0 | `-u`: Customer Credit / Vendor Prepayments cards |
| | `ncollection_subscription` | 19.0.1.18.0 | 19.0.1.19.0 | `-u` (installed on this database too: omitting it leaves a SCHEMA DRIFT error, found by the rehearsal) |
| | `ncollection_dashboard_export` | — | 19.0.1.0.0 | `-i` |
| `e2euae` (UAE e-invoicing tenant, **protected**) | `ncollection_core` | 19.0.1.27.0 | 19.0.1.28.0 | `-u` |
| | `ncollection_account_core` | 19.0.1.12.0 | 19.0.1.13.0 | `-u` (column + table, backfill 0) |
| | `ncollection_dashboard_export` | — | 19.0.1.0.0 | `-i` |
| `einvfull`, `einvdev` | as `e2euae` | | | not rehearsed separately (same module set, no data) |
| `atlas`, `ncollection_demo`, `awtar` | | `awtar` is **behind the runtime** (core 1.24, account_core 1.9, reports 1.10): it needs the earlier releases first | | not in this release's scope |

Order: backup every database → deploy code → `release status` shows each database `UPGRADE_REQUIRED` (branded 503, by design) →
per database `-u <its modules> -i ncollection_dashboard_export` (never `-u all`) → `_update_translations(['ar_001'])` for the touched
modules + `clear_all_caches` → smoke. New Arabic terms are not loaded by `-u`.

**Rehearsal results** (disposable fixture built on the runtime tree 5decb87 with invoices, a 1,000 payment on an 800 invoice, a
partial payment, a credit note, a vendor bill and an advance to the supplier; then the sprint tree):

* every database upgraded with exit 0 and no error; a **second** `-u` was a no-op (same versions, same figures);
* **existing documents backfill to `nc_overpaid_amount = 0`** (0 non-zero rows on all copies); native `amount_residual` is untouched
  (`909.3` on the accounting copy before and after; `210.0` on the UAE copy);
* the Finance dashboard on the fixture: Receivables 280 → **600** (owed) + Customer Credit **320**; Payables −310 → **400** + Vendor
  Prepayments **90**; Revenue / Net profit / Assets / Liabilities unchanged;
* the UAE copy: the three e-invoice rows (uuid, XML SHA-256, states, write date) are identical to the live database; no submission.

Rollback: restore the pre-release dump of each database and the previous source tree (the release procedure's backup volume).
