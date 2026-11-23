# Audit Log 2.0 — audit trail, version comparison and controlled Undo

Module: `ncollection_audit` (19.0.2.0.0). Built ON the existing trail. It is not a second trail: the same
`auditlog.log` and `auditlog.log.line` tables and the same value format are used. The digests, seals, retention and
the platform's read-only window all keep working.

## 1. What existed (baseline, measured on `aun` = a copy of local `ncollection`)

| Item | Before 2.0 |
|---|---|
| Engine | OCA `auditlog` (monkeypatched create / write / unlink), rules seeded for `sale.order` + every persistent `ncollection.*` model (29 rules). |
| Events on the copy | **2** (both "create"). The trail was effectively empty: partners, invoices, payments, users and products were never audited. |
| Withheld models | `res.partner` (ThrowAwayCache → KeyError), `res.users` (seat limit bypassed), `account.move(.line)` (~950-frame recursion). All were measured; see `audit_rule.py` `_NC_WITHHELD`. |
| Deleted records | `capture_record` was off on every seeded rule, so a deletion kept **no** values. |
| Company isolation | **None.** `auditlog.log` had no company field and no record rule, so any reader saw every company's events. |
| Sensitive models | `ncollection.auth.spent_token` and `ncollection.platform.entry` were audited, and their rows are (hashes of) bearer tokens. |
| Platform window | `list_audit_logs` counted rules in state `subscribed`, a state OCA 19 does not have, so it always reported 0. |
| UI | The raw OCA list and form. |

## 2. What 2.0 adds

### Capture

- **Native capture** (`models/audit_policy.py`) for the models OCA cannot audit safely. It is a normal
  `_inherit = 'base'` override, so it runs inside each model's own `super()` chain and the seat limit still runs.
- It reads only the written fields from the normal cache. There is no ThrowAwayCache and no re-entry.
- Which models are captured is data: `ncollection.audit.policy` holds the defaults below, which an Audit Log manager
  can change.
  - Contacts (customers / suppliers): `res.partner`
  - Bank accounts: `res.partner.bank`
  - Company: `res.company`
  - Users and access: `res.users`, including the `group_ids` permission changes
  - Products and categories: `product.template`, `product.category`
  - Invoices, bills, credit notes and entries: `account.move`, header fields; posting appears as a Status change
  - Payments and receipts: `account.payment`
  - Accounting configuration: `account.journal`, `account.account`, `account.tax`, `account.payment.term`
  - Analytics: `account.analytic.account`
  - Assets: `account.asset`
  - Purchase and stock: `purchase.order`, `stock.picking`, `stock.warehouse`, `stock.location`
  - HR: `hr.employee`
- Only installed models are seeded. A model an OCA rule already covers is never captured twice.
- **Never recorded:**
  - group-restricted fields;
  - binary fields and one2many lines;
  - credentials, matched by name: password, token, secret, key, signature, otp, hash, session, salt;
  - mail and activity plumbing;
  - recompute churn.
- Payloads are bounded to 4,000 characters per value and 200 fields per record. A truncated value is never restored.
- Rules seeded by the existing module now keep pre-deletion values (`capture_record`).
- The two token-bearing models are withdrawn from auditing; their past rows are kept.

### Scope and integrity

- Every event carries its document's company (`nc_company_id`), an Action (Create / Update / Delete), a finer
  document type (invoice / bill / credit note / receipt / payment / customer / supplier ...) and, when an Undo wrote
  it, a link to that Undo.
- **Record rules:** a reader sees events of their own companies, plus events with no company (shared master data).
  The rule applies to events, field lines and the Undo ledger alike.
- **Model rule:** an event about a document type the user cannot read in the ERP is not listed.
- **Document rule:** opening an event of an existing document requires read access to that document.
- **Field rule:** restricted fields are shown as "Hidden".
- **Technical metadata** (IP, request, technical model) is shown to system administrators only.
- **Events are append-only:** readers have no write or unlink, and the original event is never edited.
- **Digests:** they do not cover the new columns, so every pre-2.0 row still verifies.

### The History screen (Audit Log → History)

- **List.** Columns: View, Timestamp, User, Document, Document Type, Description, Action, and Company when the user
  has several. The timestamp is in the user's time zone with seconds.
  - The default sort is newest first, with the event id as tie-breaker.
  - Sorting by any column lasts for the visit only.
  - The list is paginated, with loading and empty states.
- **Filters:**
  - main row: From / To, User (searchable, multi-select, deactivated users included and marked), Action
    (All / Create / Update / Delete, on the stored action, not on text), Document Type (searchable, multi-select,
    only types that occur);
  - More filters: Company, Document Reference, Record ID, Search in description, and Undo (any / undone / not undone /
    Undo events);
  - active filter chips, Apply and Reset.
- **Event details:**
  - all metadata;
  - Before / After per field, with readable labels;
  - values formatted by type: money in its currency, dates, many2one and many2many names, selections, Yes/No;
  - Empty shown as such, and deleted linked records shown as "#id (deleted)";
  - Create shows the initial values and Delete the values before deletion;
  - long changes fold.
- **Record History:** a timeline of versions with action, user and time. Undone events and Undo events are marked.
- **Compare Versions:** any two versions; the "Compare with previous" link pre-selects the previous one. Results are
  changed / added / removed / not recorded, with unchanged fields hidden by default and a summary.
  - Values come only from what was recorded. The last recorded new value at or before a version wins, otherwise the
    first later event's old value.
  - A history that does not start with a creation is labelled as such, and fields nobody recorded show
    "Not recorded" rather than a guess.
  - Unrecorded changes (an event's old value ≠ the previous new value) are reported as gaps.
- **Audit History** on documents: each audited document type gets an action in its form's Action (gear) menu, for
  audit readers only. It opens the same screen on that document.

### Undo (`models/audit_undo.py`)

Undo runs as the user, through the ORM. ACLs, record rules, company checks, constraints, settings delegation and
accounting protections all apply.

| Event | Undo does | Refused when |
|---|---|---|
| Update | Writes back the OLD value of each chosen field. | The field changed again later or its current value differs (conflict, per field; other fields can still be restored); the field is restricted, computed, read-only, truncated or unsupported; a linked record is gone. |
| Create | Deletes the document. If other documents point at it, it archives it instead; this is probed in a savepoint and rolled back. | The document changed after creation; an accounting document was ever posted. |
| Delete | Creates a NEW document from the pre-deletion values. The screen says the id is new and that links are not recreated. | No values were recorded; a required link is missing; the create would fail (probed); it is an accounting document. |

Undo always refuses:

- journal items, reconciliations, bank statements and statement lines;
- taxes, assets and analytic lines;
- internal transfers, the invoice audit, invoice adjustments, cheque prints and payment batches;
- e-invoicing;
- users, groups and roles;
- the audit models themselves.

Each refusal names the correct workflow. Examples: "Use the document's own Reset to Draft, Credit Note or Reverse
Entry"; "Use Unreconcile in Bank Transactions & Matching".

Invoices and payments are restorable only while they are drafts. Any finalised state (posted, paid, done, approved,
confirmed sale / purchase, cancelled ...) is refused, and so is anything dated in a fiscal or hard lock period.

How it stays safe and traceable:

- **Preview first.** The preview shows current value → value put back, per-field status and reasons, related effects,
  and "Undo unavailable" with the reason.
- **Fingerprint.** The apply call carries the preview's fingerprint; if the document changed, it is refused.
- **Locks.** The event row and the document row are locked with NOWAIT, so a busy document is refused rather than
  waited on.
- **One Undo per event.** `unique(original_log_id)` makes a second Undo impossible, also under concurrency.
- **Undo is audited.** Each Undo creates an append-only `ncollection.audit.undo` row (who, when, which event, mode,
  result, values) and, through the ORM, ordinary audit events linked back to it. The original event shows
  "Undone by ...".
- **Rights.** Undo needs the separate group *Audit Log: Undo changes*. The Workspace Administrator implies it; reading
  the trail does not.

## 3. Migration (19.0.2.0.0 post-migrate)

Set-based SQL, safe to re-run (it fills only empty columns):

- `nc_action` is set from the recorded method;
- `nc_doc_type` is set to the recorded model (no finer type is guessed for old rows);
- `nc_company_id` is set from the document's company only where the document still exists;
- `capture_record` is turned on for the seeded rules.

Timestamps, users, values and digests are untouched. The new indexes are (create_date DESC, id DESC),
(model_model, res_id, create_date, id) and (model_id).

## 4. Known limits (stated, not hidden)

- **Before 2.0:** partners, invoices, payments and users were not recorded, so their older history does not exist.
  The screen says so instead of inventing versions.
- **Native capture records user-written fields only.** It captures the fields passed to `write`, so a stored computed
  total that changes during recompute (an invoice `amount_total`) is not a row of its own. The invoice-amount audit
  (`ncollection_account_audit`) still covers that.
- **one2many lines are not captured** (invoice lines, order lines). The parent's header fields are.
- **Record rules beyond company** (e.g. "own documents only") are enforced when an event is opened, not in the list
  count.
- **Digests:** the new columns (company, action, undo link) are not inside the per-row digest. Covering them needs a
  digest version, and that would make every existing row look tampered.
