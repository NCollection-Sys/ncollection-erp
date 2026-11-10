# Post-upgrade enhancements: requirement matrix, status, release matrix (branch `feature/post-upgrade-enhancements`)

Base: `236a7ff` (the release installed in `ncollection`, `awtar`, `ncollection_demo`). **Nothing here has been applied to a shared database or the shared runtime.** Every
result below was measured on disposable copies (private Postgres on an internal network, copies of the live databases made with a read-only `pg_dump`); "browser" means a
real Chromium (Edge) session against such a copy.

Legend: **FULL** fully implemented and tested; **LIVE** implemented, verified with the mock / local stand-in, requires live provider acceptance; **INCOMPLETE** not finished;
**DEFERRED** deferred by owner approval (none).

## Requirement matrix

| # | Requirement | Status | Evidence |
|---|---|---|---|
| 1.1 | Show / hide invoice line columns, all custom columns in the selector | FULL | optional columns; the Odoo menu check boxes are saved per user on the server; `cols.js` browser run |
| 1.2 | Free column order, reorder UI (drag and drop + arrows), saved company default, per-user override, persistence, customer invoices = vendor bills | FULL | `column_arrange.js`, `ncollection.invoice.column.pref`; browser: arrows, real mouse drag, save for me, company default, reset, hide, reload, vendor bill; 8/8 at 1440 px EN and AR, 8/8 at 768 px EN and AR; 6 server tests on resolution / ACL / isolation |
| 1.3 | Centred headers incl. Label, EN / AR RTL | FULL | SCSS; browser `text-align: center` on every header, EN and AR |
| 1.4 | Responsive desktop / tablet / mobile | FULL (scoped) | desktop + tablet: the column table and dialog; phone 375 px: Odoo shows invoice lines as cards (no table, so no column menu), the charges block and discount summary render with no horizontal overflow, EN and AR (`mobile.js`, screenshots) |
| 1.5 | Money Discount column two-way with Disc.%; fixed vs percentage mode | FULL | 18 tests + browser |
| 1.6 | Editable Amount and Total incl. Tax (inverse unit price, taxes and discount kept) | FULL | tests + real list editing (360.00 typed -> price 200, Disc.% 10 kept) |
| 1.7 | Footer charges / discounts (freight, global fixed or %) as REAL accounting lines | FULL | 15 tests: customer invoice, vendor bill, credit note, foreign currency, tax-included, rounding, several adjustments, posted refusal, balanced postings |
| 1.8 | Live recalculation while editing (not only on save) | FULL (one stated limit) | `_onchange_nc_adjustments`; browser: discount 23.00 -> 95.00 AED unsaved after a quantity change; Form-based tests. A row added just now books its line on save (amount and preview are live) |
| 1.9 | No duplicate lines, no recursion, no invalid bases, consistent journal | FULL | repeated-save test, basis excludes adjustment lines, echo of the live price not applied, every posting test checks balance |
| 1.10 | Central "Invoice Layout & Calculations" area, ACL, audit | FULL | Accounting > Configuration > Invoice Layout & Calculations: Charges & Discounts, Invoice Column Defaults (Accounting Manager; record rules + server check); chatter tracking of the definitions and of every company-default change (the tracking mechanism is wired and tested; the test transaction disables message creation, so the committed-write proof is the rehearsal shell probe) |
| 1.11 | Total Discount / Total After Discount above Untaxed Amount | FULL | browser EN / AR |
| 1.12 | Posted-invoice protection not weakened | FULL | refusal tests; core posted-line behaviour unchanged (policy decision stays separate, INVOICE_AMOUNT_AUDIT.md section 7) |
| 2 | Anomaly Alerts removed everywhere, migration-safe | FULL | model, detectors, crons (and their server-action external ids), menu, views, rules, ACLs, Home tile removed; verified on a FRESH database (platform + tenant modules) and on copies of all three databases: table, ir_model, crons, menus, actions, views, ACLs, rules all 0; history archived to `ncollection_alert_archive` before the table drop (test with rows); idempotent; release health, audit modules, scheduler identity and other alerts untouched; `test_anomaly_alerts_removed.py` |
| 3.1 | Odoo 20 AI research (authoritative sources, licensing) | FULL (with a stated evidence limit) | `AI_ODOO20_RESEARCH.md`: public Community repo has no AI module on 19.0 / 20.0 / master; the Enterprise-only / credits statements come from third-party summaries and are marked unverified; no Enterprise code read or copied |
| 3.2 | Read-only, privilege-respecting useful answers | FULL (mock) | `ai_topics.py` (payables, overdue, sales, purchases, payments, stock; EN + AR); runs as the asking user; PII tokens; 166 + 55 gateway tests |
| 3.3 | Real conversational model | LIVE | gateway has `mock` and `anthropic`; no credential, no call made; exact configuration and 6-step acceptance in `AI_ODOO20_RESEARCH.md` section 6 |
| 3.4 | Model vs mock vs deterministic summary are distinguishable | FULL | each assistant message stores `origin` (`model` / `demo`) and model id; the widget prints "AI model: <id>" or "Demo mode: a summary of your data, not an AI model"; 3 tests |
| 3.5 | Animations (bubble, panel, messages), reduced motion, RTL, mobile | FULL | CSS; browser EN / AR: enter / exit transitions, icon rotation, Escape, focus return, reduced motion, RTL origin, mobile sizing |
| 4 | UAE e-invoicing audit, local demo in `ncollection_demo`, no provider submission, protected invoices | FULL (local); A-Cube LIVE | installed on a copy of `ncollection_demo` only; menus under Accounting > UAE E-Invoicing; fail-closed licence proven ("not licensed" until the mirror names the module); seed with the labelled `LOCAL-UAE-DEMO` mirror (the same record config-sync writes, enforcement code unchanged, refuses any database but `ncollection_demo`); local flow with the official validator: draft -> ready -> locally checked, submission `not_submitted`, XML archived; role visibility measured; A-Cube never contacted; `e2euae` untouched, attachments 548 / 550 / 551 checksums identical |
| 5 | Dashboard PDFs: A4, margins, no clipping, tables, currency, EN / AR | FULL | Sales, Invoicing, Summary, Spreadsheet Dashboards x EN / AR rendered: A4 210 x 297 mm, first text 16 mm from the top, 14 mm side margin, repeated headers, 1-2 pages, "4,901 AED"; RTL footer "page n / N" fixed in this round; Excel unchanged. The Accounting dashboards (Finance / Accountant / Cash / CEO) have no PDF export of their own; the Reports hub PDFs use their own paper formats and were not changed |

## Tests run on the final code (private stack, fresh database, no unrelated large suites)

* Focused regression, one run: invoice layout (48), invoice amount audit, AI (+ origin), core (incl. anomaly removal, role matrix, release gate), branding (incl. translation coverage),
  account reports (premium exports), account core (payment allocation, line contract), dashboard export, bank reconciliation + sales action, UAE e-invoicing + localization,
  sale / purchase documents: **2200 tests, 1 failure** (a core test that pinned the invoice line column list and did not know the layout module's columns). Fixed; the class was re-run: 2 of 2 pass.
  The remaining "ERROR" lines in that log are the tests' own deliberate faults (duplicate-key probes, a forced HTTP 500, a rejected ASP adapter).
* No skipped test was counted as a pass; no skip was added to `expected_skips.txt`. (The word "skipped" in the log belongs to role-link install messages.)
* Static gates at the final commit: flake8, invariants (+tests), architecture guard (+tests), skip gate tests, role matrix (+tests), doc counts (+tests): green; pylint-odoo 205 / 205 baseline.
* Not re-run, on purpose: the full CI matrix and `make verify-all` (unrelated suites: routing, provisioning, config-sync, cron, e2e).

## Final upgrade rehearsal (copies of the three live databases; nothing shared touched)

Matrix: `-u ncollection_core,ncollection_branding,ncollection_account_reports,ncollection_dashboard_export,ncollection_ai,ncollection_account_audit -i ncollection_account_invoice_layout`
(+ `ncollection_account_localization_uae`, `ncollection_uae_einvoicing`, `ncollection_uae_einvoicing_acube` on `ncollection_demo` only).

| Database | pass 1 | second identical run | schema drift | pending states | unbalanced moves | Anomaly objects |
|---|---|---|---|---|---|---|
| `ncollection` | rc 0, 67 s, setup assert clean | fingerprints (modules, columns, row counts, residuals) identical | 0 | 0 | 0 (46 posted) | 0 |
| `awtar` | rc 0, 58 s | identical | 0 | 0 | 0 (2 posted) | 0 |
| `ncollection_demo` | rc 0, 66 s | identical | 0 | 0 | 0 (6 posted) | 0 |

Arabic terms loaded (`_update_translations`); core 19.0.1.29.0, AI 19.0.1.4.0, layout 19.0.1.1.0. The e-invoice local demonstration was then seeded on the `ncollection_demo` copy (twice: the second
run changes nothing), exercised and shown in the browser.

## Release matrix for the shared upgrade (NOT executed, awaits owner approval)

Per database, in this order: fresh backup + manifest; pass 1 (the `-u` / `-i` above); pass 2 `-i ncollection_account_audit` only if it is not installed (it is, on all three); Arabic terms; schema drift (0);
release gate READY; browser acceptance. Only on `ncollection_demo` afterwards: start `einvoice-validator` and `einvoice-validator-sb`, run `scripts/upgrade/seed_uae_einvoice_demo.py`
(licence mirror `LOCAL-UAE-DEMO`, owner to confirm), never any provider. `e2euae` and every other database stay out of every command.

## Rollback and recovery

* Per database: restore the pre-upgrade dump taken immediately before (`pg_restore` into a fresh database, filestore tar alongside); the code rollback is `git checkout 236a7ff` plus a container restart.
* What the upgrade changes that a restore reverts: schema (new tables `ncollection_invoice_adjustment*`, `ncollection_invoice_column_pref`; new columns on `ncollection_ai_chat_message`;
  `ncollection_alert` dropped after being archived), data (the Anomaly Alerts menu / crons), module states. `ncollection_alert_archive` lives in the database and is restored with it.
* A half-applied module (`to upgrade` / `to install`): RUNBOOK_RELEASE_UPGRADE.md; never `-u all`.
* Pre-upgrade backups of the current state: `D:\Backups\upgrade-pre-20261107` (verified, 11 files); fresh ones are taken again before the real upgrade.

## Follow-up releases after the consolidated upgrade

| Commit | What | Shared runtime |
|---|---|---|
| `0239d0a` | the consolidated release above | applied 2026-11-10 (three databases) |
| `9ed9f9a` | a money Discount typed in the invoice form was discarded (onchange read the value after changing the mode) | applied 2026-11-10, invoice layout 19.0.1.1.1 |
| `e53725d` | a quantity save turned a fixed discount into a percentage; a total that only followed an edit re-solved the price; the price solver's slope came from rounded probes (200.32 for 200.00) | awaits the 6 GB commit-headroom gate; invoice layout 19.0.1.1.2 |
| payment duplicate + receipt layouts (branch `feature/payment-duplicate-receipt-layout`) | Duplicate on customer / vendor payments; the Payment Receipt under every Configure Document Layout choice | NOT applied; needs a separate approval; accounting core 19.0.1.14.0 |

Lesson recorded with `9ed9f9a`: `odoo.tests.Form` sends more fields than the web client does. A typed-column fix is proven
with the real browser on a private copy BEFORE it is proposed for the shared runtime, and regression tests replay the
client's real payloads.

### Release matrix for the payment / receipt release (NOT executed)

Per database (`ncollection`, `awtar`, `ncollection_demo`), after a fresh backup and with at least 6 GB commit headroom:
`-u ncollection_account_core` (and `ncollection_account_invoice_layout` if `e53725d` has not been applied yet), Arabic
terms, schema drift 0, release gate READY, browser acceptance (Duplicate on a draft and on a posted payment, the receipt
PDF in the company's layout). No new table or column; one new view (`view_account_payment_form_nc_duplicate`).

### Rollback of the follow-up releases

Code: `git checkout` the previous commit of the table above and restart Odoo. Data: each step has its own verified
backup set (`D:\Backups\upgrade-pre-20261110` before `0239d0a`, `D:\Backups\hotfix-pre-20261110` before `9ed9f9a`; a
fresh one is taken before each further step). The invoice-layout fixes and the payment / receipt release change no stored
data on upgrade, so rolling the code back and running `-u` on the same modules is enough; a database restore is needed
only if documents created after the upgrade must be discarded as well. Payments created with Duplicate are ordinary
drafts and survive a code rollback.

## Limits and decisions that remain

* Phones: the invoice line cards have no column table, so the arrangement does not apply there (Odoo's own mobile layout).
* A newly added charge row books its invoice line on save; everything else recalculates live.
* Real AI provider and A-Cube sandbox: not authorized, not contacted (LIVE rows).
* The `LOCAL-UAE-DEMO` mirror is written by a local script into `ncollection_demo` (a platform database that has no tenant of its own); the platform's own tenant / plan route is not used. The owner confirms or rejects this before it runs.
* Windows host trust and unrotated credentials: unchanged and out of scope here.
