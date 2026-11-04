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
