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
| A-Cube e-invoicing adapter proven on the sandbox | `UAE_EINVOICING.md` §9f: built against the published OpenAPI, "NOT yet proven on its sandbox (no credential), so release stays blocked". Needs an A-Cube credential; none is available and none may be used. Scope check done: `ncollection_uae_einvoicing` and `_acube` are installed in none of the three target databases and are GATED modules (UAE country + verified UAE books + Accounting + an explicit per-tenant enable, never in a plan), so this upgrade does not install or change them and the block does not stop it. The e-invoicing release itself remains blocked. |
| P8-T05 / `#81` invoice-amount audit criterion | **Delivered by `ncollection_account_audit` and part of the release matrix (pass 2 on all three databases).** An audit entry with old / new values, user, IP, timestamp, company and invoice identity is written for each net change of an invoice's amounts (`INVOICE_AMOUNT_AUDIT.md`). Verified: 20 module tests; focused accounting regressions (602 + 324 tests, with `sale` and the OCA report dependency installed); fresh restore of all three databases, install in pass 2, idempotent, no schema drift; the real UI (edit an invoice line in the form, open the entry from the Accounting menu in English and Arabic); unauthorized users refused. Open policy decision (not a defect of ours): stock Odoo 19 lets a posted line's price change without re-pricing the booked entry. |
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
* A transcript is a convenience, never evidence (cron-deleted after `ncollection_ai.transcript_retention_days`, default 90, `0` keeps forever).
* `ncollection_demo` and the chat widget: RESOLVED. The gateway previously refused database names with an underscore. It now accepts ASCII letters, digits and underscores (not first), 1-63 characters; authentication, tenant binding and per-tenant keys are unchanged (tests: positive and negative names, no cross-tenant impersonation). The widget is installed and accepted (EN + AR, streamed) on all three databases with the mock provider.

## 7. Streaming implementation review (worker exhaustion, timeouts, authorization, throttling, data exposure)

| Concern | Finding | Evidence / action |
|---|---|---|
| Worker exhaustion | **Real problem, now bounded per database AND server-wide** (see README "Server-wide admission": cap = half of `workers`, never more than `workers - 1`, bounded wait 300 ms, fail closed; 3 tenants streaming on `--workers=3`: 1 admitted, 2 refused in under 0.8 s, ordinary requests 19 ms). Original finding: An AI request, batch or streamed, holds one web worker for the provider's latency; workers are shared by all tenants (production `workers = 4`). | Measured on `--workers=2`, 7.7 s answers: two concurrent streams delayed an ordinary request by **6,175 ms** (idle: 20 ms). Fixed with a per-database cap (`ncollection_ai.max_concurrent_requests`, default 2, Postgres advisory lock per slot): with the cap at 1, the second stream was refused in **52 ms** and the ordinary request took **25 ms**. Tests: `TestConcurrencyCap`, the route test, `worker_probe.js`. Residual: the cap is per tenant, so many tenants asking at once can still fill the pool; size `workers` accordingly. |
| Timeouts | Odoo reads with a 30 s idle timeout and a 512 KB total cap; the gateway bounds a provider stream at 60 s and 64 KB; production `limit_time_real` is 1200 s, never reached. A stalled provider ends the stream with an error line and saves nothing. | `TestStreamingPlumbing`, `acceptance_stream.js` (gateway stopped mid-use). |
| Authorization | Same code as the batch RPC (gate, #375 flag, caps, secret scans, registry lookup) before the first byte; `auth='user'`; Odoo's CSRF token required (a request without it is refused); a plain user gets 403; anonymous is redirected. | Module tests + browser (plain user 403, no-token 4xx). |
| Throttling | Gateway: per-tenant token budget, request rate, circuit breaker, all decided before the first byte. **Gap found and fixed:** a stream that ended early was not charged at all, so cancelling after the first chunk was free model use. It is now charged the estimate for what was produced. | `TestAbandonedStream`. |
| Sensitive data | The gateway sees only the sanitised prompt (identity tokens, not names); its log line is metadata only; the stream endpoint returns re-hydrated text to the authorised user only (the same text the batch path returned); nginx's access log records the route, not the form body; the CSRF token and key never appear in a response. No new exposure found. | Source review + `test_isolation` / gateway tests. |
| Not proven | Live provider streaming and the model's obedience to the answer-language instruction. | Section 2. |

## 8. Rehearsal findings that change the upgrade plan

* **The release gate requires modules that `-u` does not install.** After the planned `-u`/`-i` lists, `ncollection_demo` answered a branded 503 (`UPGRADE_REQUIRED`,
  `absent=ncollection_sale_attachments,ncollection_stock_barcode`): both are `auto_install` with `sale` / `stock`, which were already installed, so Odoo never
  triggered them. They must be listed with `-i`. `ncollection` and `awtar` already had them.
* **Two OCA repositories must be added to the runtime's `./oca`** (`account-reconcile` @ e3d99f3d, `bank-statement-import` @ aa647ee5; tars in the recovery folder) and
  the release `odoo.conf` (two more `addons_path` entries) must be live in the container: recreate it, never restart (#427).
* `ncollection_demo` is classified by the gate as a PLATFORM database (it has `ncollection_saas` installed), so it is gated like one.
