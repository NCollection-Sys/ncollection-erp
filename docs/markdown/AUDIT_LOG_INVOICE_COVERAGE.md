# Audit Log 2.0 — invoice coverage: findings and LOCAL-ONLY proposal (not implemented, not deployed)

The evidence is a rolled-back experiment on the local copy `aun` running release 03f2ef4b (the production code):
`D:\Backups\audit-log-2-rc\invoice_coverage_experiment.txt`. Steps: draft invoice, then header reference, then line
description, quantity, unit price, discount, taxes, product, then a second line added through the invoice, then
posting.

## 1. Coverage matrix (what is recorded TODAY)

| Change | Audit Log 2.0 (`auditlog.log`) | Invoice Amount Audit (`ncollection.invoice.audit`) |
|---|---|---|
| Invoice created | yes: create event with the header fields | yes: "created", amounts 0 → total |
| Header fields (partner, dates, reference, payment terms, journal, currency, notes) | yes: field, before, after | no |
| Status / posting (`state`, `posted_before`, `invoice_date` set at posting) | yes | no (amounts unchanged) |
| Line **description** | **no** | **no** (no amount effect) |
| Line **quantity** | **no** | total only (100 → 300); does not say which line or field |
| Line **unit price** | **no** | total only (300 → 360) |
| Line **discount** | **no** | total only (360 → 324) |
| Line **taxes** | **no** | untaxed / tax / total (324 → 340.20) |
| Line **product** | **no** | total only (340.20 → 39.69, the product re-priced the line) |
| Line added / removed | **no** | total only |
| Line subtotal | **no** | no (only invoice totals) |
| Invoice untaxed / tax / total | **no** (stored computed, never in `write` vals) | **yes**: old and new, user, IP, digest |
| Deleted draft invoice | yes: pre-deletion header values | yes: "deleted", amounts → 0 |

## 2. Answers

1. **Invoice fields in Audit Log 2.0:** header fields written by the user, plus the status at posting. Lines
   (one2many) and the computed amounts are not recorded.
2. **Fields only in the Invoice Amount Audit:** amount untaxed, tax, total and the lines total, with old and new
   values per outermost operation, plus the user, IP and a content digest.
3. **Line traceability:**
   - Product, description, quantity, unit price, discount, taxes and subtotal are **not traceable at field level**
     anywhere.
   - Quantity / price / discount / taxes / product are only visible **indirectly**, as a change of the invoice total,
     and it does not say which line or which field.
   - The description is not traceable at all.
4. **Version comparison of financial changes:** **no.** Compare Versions shows header fields only. The amount history
   is a separate list (Accounting > Invoice Amount Audit) with no version comparison.
5. **Can the detail view safely show or link it?**
   - **Yes.** Both sources are append-only, company-scoped tables.
   - The Audit Log service already applies company, model and document read rules. The same checks can read the
     amount rows of the invoice being viewed (sudo read, filtered by `move_id` / `res_id` and the reader's own access
     to the invoice).
   - Nothing would be written or copied.
6. **Gaps, duplicates, timestamps:**
   - **Duplicate:** every amount-audit row is ALSO recorded in Audit Log as an event "Create Invoice Amount Audit"
     (the OCA rule seeded on `ncollection.invoice.audit`, because it is an `ncollection.*` model). The History screen
     shows those raw rows as a separate document type. This is confusing noise, not lost data.
   - **Timestamps are not on one clock:**
     - Audit Log events use the database **transaction start** time (`create_date` = `now()`): every event of one
       request carries the same timestamp.
     - The amount audit uses the **application wall clock** at the moment of the change (`fields.Datetime.now()`).
     - Within one request they can differ by the request's duration, and the amount audit has second precision
       only. Ordering inside one transaction must therefore use the event id, not the time.
   - **Noise:** `is_manually_modified` is recorded with header changes (Odoo bookkeeping).
7. **Shortfalls against the original requirement ("exactly what changed", "version comparison"):** yes, for invoices.
   - Line-level changes (product, description, quantity, price, discount, taxes, subtotal) are missing.
   - Amounts are not part of the record history or the comparison.
   - Customers, suppliers, payments, products and settings meet the requirement. Invoices meet it for the header and
     the status only.

## 3. Proposal (LOCAL ONLY — awaiting the owner's review)

Principle: **one screen, two reliable sources, no new duplicate store.** The Invoice Amount Audit stays the single
authority for amounts. Audit Log 2.0 adds only what nobody records today: field-level changes of invoice lines.

**P1. Invoice line capture (native, narrow).**
- Native capture on `account.move.line` limited to:
  - lines of an invoice / receipt with `display_type = 'product'` (tax, payment-term and rounding lines are derived);
  - fields `product_id, name, quantity, product_uom_id, price_unit, discount, tax_ids, analytic_distribution`, as the
    user writes them;
  - create and unlink of such lines.
- `price_subtotal` / `price_total` are taken **only if already in cache** (the capture never computes).
- Each event also stores the parent invoice (`nc_parent_model`, `nc_parent_res_id`), so the invoice's own Record
  History shows its line changes.
- **Why this is safe where OCA was not:** the OCA recursion on `account.move.line` comes from its monkeypatched write
  with full before/after re-reads inside a throw-away cache. The native path is an ordinary `_inherit` override, reads
  only the written fields from the cache, never computes and never creates environments. These are the properties
  that already fixed the sales-order case.
- **Cost:** Odoo writes lines internally on every recompute (balance, tax lines). Those are not user fields and are
  not product lines, so they are skipped by the field filter; the cost is one dict lookup per write.
- Measured acceptance: the full CI suite, plus a 300-line invoice edit benchmark.

**P2. Amounts in the same history (read, not copied).**
- The History / Record History / Compare service reads `ncollection.invoice.audit` rows of the invoice and merges
  them into the timeline as "Amount changed" entries, ordered by (time, source, id), labelled with their source.
- Compare Versions gains three rows: Untaxed, Tax, Total. They come from the amount audit at the version's time, or
  "Not recorded" if none applies.
- Undo never acts on these rows: amounts are never restored, the lines are.

**P3. Remove the duplicate.**
- Withdraw the OCA rule on `ncollection.invoice.audit` (it is its own append-only audit) through the same
  `SENSITIVE_MODELS` / withdraw path used for e-invoices.
- Historical events stay. They are hidden from the default History filter but remain searchable.

**P4. Noise and timestamps.**
- Add `is_manually_modified` to the noisy fields of `account.move`.
- Show source timestamps as recorded, with the precision of each source.

**P5. Undo for lines.**
- Field restore on lines only while the invoice is draft and not locked (the same rules as today), as the user,
  through the ORM.
- Re-adding a deleted line is refused (amounts and taxes would be recomputed: use the form).

**Estimate:** about 1 day of build and tests locally, plus the full suite, review and browser acceptance. Version bump
`ncollection_audit` 19.0.2.1.0 (no data migration; the new parent columns are nullable).
