# Local release: scope status (2026-11-06)

What the one consolidated local release contains, and for each item whether it is **completed and tested**, **implemented but not
fully accepted**, **unfinished**, or **explicitly deferred by prior agreement**. Sources are named; nothing here is promoted to
"done" on the strength of a server test alone when the requirement is a browser behaviour.

Release line: `feature/63-p5-t06-ai-chat-widget-integration` (it contains `feature/bank-reconciliation` @ `24897a8`, which
contains the Option A / Platform UX line). Target databases for the local upgrade: `ncollection` (platform), `ncollection_demo`,
`awtar` only. The shared upgrade is NOT authorised yet and has not started.

## 1. Completed and tested

| Item | Evidence |
|---|---|
| Bank Transactions and Matching on the OCA engine (import, matching, drag and drop, discounts, Sales action) | `docs/markdown/BANK_RECONCILIATION.md`; accepted by the owner |
| Dashboards: premium design system, KPI corrections, context band, PDF / Excel export, Sales and Invoicing analysis | `docs/markdown/DASHBOARD_ACCEPTANCE_MATRIX.md`; headless browser acceptance |
| Overpaid settlement, filters, DB sorting, Owner document layout, Duplicate, chatter collapse | release commits; module tests; Arabic terms |
| Option A tenant Owner least-privilege, Platform UX sprint | `docs/markdown/RELEASE_OPTION_A_AND_PLATFORM_UX.md` |
| AI chat widget: curated prompts in five contexts, gate, private history, escape-first renderer, Arabic / RTL, XSS and isolation | `e2e/ai/acceptance_chat.js`, `acceptance_contexts.js`; module tests |
| AI chat widget: **streaming** (gateway `/v1/stream`, Odoo route, incremental rendering, fallback to batch) | `e2e/ai/acceptance_stream.js`; gateway and module tests; see section 5 for what is NOT proven |
| AI chat widget: no ORM forgery, server-side size caps, configurable transcript retention (cron), Arabic transcript and answer language | module tests (`test_chat_hardening.py`); browser checks |

## 2. Implemented but not fully accepted

| Item | What is missing |
|---|---|
| Streaming against a REAL provider | The Anthropic SSE parser is unit-tested against recorded frame shapes only. No provider key exists here and no external call is allowed, so it has never run against the live API. The mock provider proves the plumbing, not the provider wire format. |
| AI answer language | The prompt instructs the provider to answer in the viewer's language (tested: instruction present, canonical English question still sent). Whether a real model obeys is not provable offline. |
| Local upgrade of `ncollection_demo` / `awtar` | The Option A gate recorded both as "far behind (up to 13 modules)". A rehearsal on copies of both, with their exact `-u` lists, is required before the shared upgrade; the earlier rehearsal covered ten other databases. |
| Browser review of everything on the upgraded shared runtime | By design happens after the shared upgrade, which is not authorised yet. |

## 3. Unfinished

| Item | Why |
|---|---|
| A-Cube e-invoicing adapter proven on the sandbox | Needs an A-Cube credential; not available and not permitted. Built against the published OpenAPI only. The e-invoicing release stays blocked on this; it does not affect the three target databases (the engine is a gated module, not enabled for them). |
| `#81` invoice-amount audit criterion | `auditlog` cannot audit `account.move` (recursion, #431); the task was re-scoped and the criterion is recorded as not met. Financial audit UX is `#124`. |
| Host security gate | Tamper Protection and cloud-delivered protection were off at the last check; credentials not rotated from a clean device. Blocks the shared upgrade, not development. |

## 4. Explicitly deferred by prior agreement

| Item | Where agreed |
|---|---|
| CODA bank statement import | `BANK_RECONCILIATION.md`: no maintainable 19.0 parser; files are refused with a clear message |
| Workspace "Pending approvals" widget | Odoo Enterprise `approvals` model is absent from Community; widget omitted by design (`DASHBOARD_ACCEPTANCE_MATRIX.md`) |
| Phase 9 (marketplace) | After Phase 10 (project plan) |
| Repair of the eight `ncollection_demo` filestore defects | Accepted by the owner as pre-existing; do not repair in this release |
| Deletion of any database | Separate approval of an exact list |

## 5. P5-T06 acceptance, item by item

| Requirement | Status |
|---|---|
| Persistent OWL widget, floating, minimise / expand | completed and tested |
| Message history per user | completed and tested (read-only ACL, own-rows rule, retention) |
| Markdown rendering | completed and tested (escape-first subset) |
| Suggested prompts per module context | completed and tested in the browser: default, sales, CRM, accounting, inventory (English); Arabic: see `e2e/ai/README.md` |
| Streaming responses | implemented; proven end to end against the mock provider only |
| Talks only to the P5-T02 gateway | completed (signed requests; key from the environment) |
| Brand styling, five modules demoed | completed and tested |

### The streaming decision

`AI_PLATFORM_DESIGN.md` §6 prescribes streaming for the chat and names one hazard: a held-open connection on the **cron worker**
is the #310 starvation pattern. The implementation does not use the cron worker. A streamed answer holds one **web worker** for
its duration (a few seconds; the gateway bounds a stream at 60 s). The route is `POST /ncollection/ai/chat/stream` with Odoo's own
CSRF token; admission (gate, #375 flag, caps, secret scans, budget, circuit breaker) is decided before the first byte; the answer is
saved once, only after the provider's final `done`; the response carries `X-Accel-Buffering: no`. Operators who run few workers
should know that N concurrent streams hold N workers.

## 6. Unresolved items left by the AI widget work

* Streaming is not proven against a live provider (see section 2).
* A user can read their own transcript rows but the transcript remains a convenience, never evidence (cron-deleted after
  `ncollection_ai.transcript_retention_days`, default 90, `0` keeps forever).
