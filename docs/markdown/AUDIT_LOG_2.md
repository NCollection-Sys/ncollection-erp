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
- **A reader who can open a document type sees the names of all its events** (list and search), even when their
  "own documents only" record rules hide some of those documents. Opening an event still checks the document. Audit
  readers are Workspace Administrators and system administrators. Over raw RPC, the company rule and the
  restricted-field rule apply at the ORM; the document-type filter is enforced by the service.
- **An Undo preview really runs the delete / create in a savepoint and rolls it back.** A rolled-back create can
  consume a sequence number (a gap) — the same as when a user opens and discards a form.
- **The creation record is taken where the ORM creates the row.** Values a model sets after its own `create`
  (for example an invoice's computed amounts) appear in that document's later events, not in the creation record.
- **Invoice amounts, invoice lines and reconciliations are not in this trail.** Amount changes are in the Invoice
  Amount Audit (`ncollection_account_audit`), and reconciliations are in Bank Transactions & Matching.

## 5. Independent reviews (on 883116a0) and what was changed

| Review | Finding | Resolution |
|---|---|---|
| Odoo architecture H1 | Any model (even `auditlog.log` itself) could be given a policy → endless recursion | Reserved models are refused by a constraint, by seeding and by the capture itself (`BLOCKED_PREFIX`, `SENSITIVE_MODELS`, platform-extended models). Test added. |
| Odoo architecture H2 | `ondelete='restrict'` from the Undo ledger would block retention for good | The ledger keeps `original_log_ref` (unique) and the link is `set null`. Test: prune an undone event; the ledger and the one-Undo guard survive. |
| Odoo architecture H3 / accounting M6 | Writing an audit row could abort the business write | `Log.create` runs inside a savepoint; a failure is logged and the business write continues. |
| Odoo architecture H4 | Restricted-field lines could be read over raw RPC | The line record rule now requires the field's group (in the same rule as company scope, because group rules are ORed). |
| Odoo architecture M1 / M2 / M6 | Per-field SELECTs; per-row scope lookup; duplicate actions across workers | Full snapshots prefetch; company and type are computed in the capture; duplicate actions are removed. |
| Security F1 (HIGH) | The client context key `nc_audit_skip` / `install_mode` switched recording off | Recording is suppressed only by a server-side thread-local (`audit_flags.py`), set by the rolled-back probes. Test: a client context key still records. |
| Security F2 | A context key forged the Undo attribution | The Undo id comes from the same server-side scope. Test added. |
| Security M1 / M4 / L1 / L4 / L8 | One-company `company_ids`; ids leaked in conflict messages; unhashable input gave a 500; removed fields were not masked; the manager group was outside the rules | All fixed. |
| Accounting H1 (HIGH) | Odoo 19 payment states (`in_process`, `canceled`, `rejected`) were not final → a posted payment's amount was restorable | Invoices and payments are restored only while draft, and only while their entry is a draft. The states are added. Test added. |
| Accounting M1 / M2 / M3 / M4 / M5 | Locks; lock dates restorable; master data reclassification; archive fallback; per-company fields | All lock dates are checked. Lock / fiscal / chart / currency fields, account type and journal type / account are never restored. A referenced accounting master record is never archived. Per-company fields are refused. |
| UI/UX | Tablet columns; untranslated labels; blank restore value; ARIA; contrast; row click; version headers | All HIGH and the listed MEDIUM items are fixed. |

## 6. Invoice coverage (19.0.2.1.0, LOCAL — see AUDIT_LOG_INVOICE_COVERAGE.md for the investigation)

**Invoice lines, field by field**
- Lines are recorded natively: `account.move.line`, invoice and receipt **product** lines only.
- Recorded fields: product, description, quantity, unit, unit price, discount, taxes, analytic distribution.
- Lines added and removed are recorded too (pre-removal values kept).
- Each line event carries its invoice (`nc_parent_model` / `nc_parent_res_id`), so it appears in the invoice's Record
  History, opens the invoice, and shares its access rules.
- Tax lines, payment-term lines and journal-entry lines are derived by Odoo and are never recorded.

**Amounts**
- The Invoice Amount Audit stays the **authority** for amounts. Its rows are READ into the invoice timeline and into
  Compare Versions (Untaxed / Tax / Total at the end of each version), never copied.
- Its OCA copy rule is withdrawn, so each amount change is no longer shown twice. Old copies stay in the table and are
  only hidden from the History list.

**Timestamps and noise**
- Each source keeps its own true timestamp: audit events carry their transaction's start, amount rows the moment of
  the change.
- A version's amounts are the last amount recorded before the next audit event begins. Nothing historical is
  rewritten.
- `is_manually_modified` is no longer recorded.

**Undo of lines**
- Allowed only while the invoice is a draft and outside locked periods: restore fields, or remove a line that was
  added.
- A removed line is never re-added.
- A posted, paid, reconciled or locked invoice is refused, with a pointer to the credit-note workflow.

## 7. Release review of the printable versions (2026-11-27)

Fixed before release: a document's own History now compares PRINTED versions (it showed only the field
table); a restored partner / fiscal position keeps the taxes of the lines exactly as the preview showed them;
a foreign-currency preview shows company-currency signed totals; frame styles load from the host the user is
on; the reader is checked before anything is said about a record; write access is checked before a row is
locked; many2many restore targets need read access; frames rescale when the application sidebar folds.

Known limitations (documented, not changed):
- A version preview lays the historical values over the document inside a savepoint that is always rolled
  back. While it renders (seconds at most; `lock_timeout` 1.5 s for the reader) the rows it overlays are
  locked, so a concurrent edit of that document waits. Nothing is written or leaked.
- Restoring an older date is not refused by the lock-date check (it checks the document's current dates); the
  draft is re-checked by Odoo when it is posted. A draft's sequence name can be restored (Odoo's uniqueness
  constraint still applies).
