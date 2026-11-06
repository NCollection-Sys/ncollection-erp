==============
NCollection AI
==============

Tenant-side context injection for LLM prompts (P5-T03 / #60).

What it does
============

Builds tenant-scoped context from ERP aggregations, sanitises PII **before the
data leaves the database**, sends the sanitised prompt to the AI gateway
satellite over HTTP, and re-hydrates identities in the response.

Runs on **tenant databases only** (``DELIVERABLE_1_SYSTEM_DESIGN.md:244``). The
component that performs the actual outbound call is a separate satellite
container holding no database credentials — see ``satellites/ai_gateway/``.

Why sanitisation happens here
=============================

``AI_PLATFORM_DESIGN.md`` §5: *"Sanitise tenant-side, before transit. This is
the whole reason context is built in the tenant DB. Once data reaches the
gateway it has already left the tenant boundary; scrubbing there would be
theatre."*

Nothing downstream re-checks it, and nothing downstream *can*: the gateway holds
no database credentials and cannot tell a real IBAN from a plausible string.

Two tiers of protection
=======================

**By field name** — structured data. Secrets (``api_key``, ``password``,
``iban``…) are redacted; identities (``partner_id``, ``company``,
``commercial_partner_id``…) are pseudonymised to stable per-request tokens.

**By shape** — free text, which field names cannot reach. Credentials (JWTs,
``sk_``/``AKIA``/``ghp_`` prefixes, ``Bearer`` headers, connection strings, PEM
blocks), IBANs, card PANs and government-issued identifiers are redacted
wherever they appear; known partner and company names are pseudonymised.

The second tier exists because the module's own entry point, ``ask(question)``,
takes free text. Reviewers demonstrated that a customer name, an API key, a JWT
and a passport number all reached the provider verbatim when only the first tier
existed.

On top of both, ``ask()`` **refuses** a question that looks like it carries a
credential rather than trying to scrub it — see *Known limits* for what that
does and does not buy. Scrubbing an arbitrary string cannot be made complete,
so the boundary is drawn by refusing rather than by cleaning.

Who may ask
===========

``ask()`` is gated to the **Accountant**, the **CEO** and the workspace owner
(``base.group_system``). The default context carries receivables and invoice
history, so it takes the same gate as the financial dashboards.

The gate lives in the method, not on a menu, because the module ships no menu
yet — ``ask()`` is a public ``@api.model`` reachable by RPC today. Relying on
the aggregation engine's per-model ACL check is **not** sufficient: core grants
``group_sale_salesman`` read on ``account.move``, so a Sales user would have
passed it. That exact claim was disproved for the financial dashboards in
``9bb86e7`` (#358).

Known limits
============

* **Natural-language questions are OFF by default.** ``ask()`` refuses free
  text unless ``ncollection_ai.enable_free_text_questions`` is set. This is a
  scope decision: P5-T03 is the *Context Injection Engine*, and its acceptance
  criteria never included arbitrary end-user prose. Eight review rounds
  established that filtering such prose cannot be made both safe and usable —
  ``"the wifi password is sunshine"`` must be refused and ``"the token is
  stored securely"`` must not, and those are the same sentence structurally.
  Turning it on is a deliberate act, taken with the provider terms it depends
  on. See issue #375. The P5-T06 chat widget ships around this, not through
  it: its default path sends **server-owned** curated prompts (below), and
  its typed input only exists once this flag is deliberately on.

* **Behind that switch, the free-text filter is best-effort.** It refuses a
  credential noun (``password``, ``key``, ``login``, ``cvv``, ``recovery
  phrase``…, including plurals and compound forms like ``DB_PASSWORD``) whose
  value is credential-*shaped* — letters+digits, a short digit run, length ≥12,
  or mixed case. Also refused: an unspaced ``=`` assignment, and any single
  token over 19 characters mixing letters and digits.

  **Not caught** — pinned by
  ``test_the_known_residual_is_what_the_documentation_says_it_is``, which
  asserts these still travel, so closing one *fails that test* and forces this
  list to be corrected rather than drifting:

  - a credential noun outside the list (``"the doorcode is 4521"``)
  - no noun at all (``"check correcthorsebatterystaple for me"``)
  - a short, single-case, dictionary-word value (``"the password is dragon"``)
  - connectors spelled as words the module does not know (``"password equals
    X"``, ``"the api key -> X"``)

  **Known false refusals**, accepted as the fail-safe direction: an ordinary
  word of 12+ characters directly after a credential noun (``"the password is
  confidential"``), and ERP references with interleaved digits beside a
  credential noun (``AC2026Z9``, ``TXN2026Q1``). ``login history2026`` and
  ``login AB123456`` are *not* refused — their digits are a trailing run. This
  cost is why the feature is opt-in rather than on.

* Long hex strings are redacted, so a question about a **checksum** returns
  ``[REDACTED]``. Deliberate: no shape separates a SHA-1 digest from a 40-char
  HMAC key, and §5's "never send" for secrets is unconditional.

* The free-text identity scan is bounded (``_IDENTITY_SCAN_LIMIT``). A partner
  outside that window is not pseudonymised in free text. The structured path
  remains the primary control.

* **Only ``ask()`` is public.** ``_build``, ``_sanitise``, ``_rehydrate`` and
  ``_complete`` are underscore-prefixed so Odoo's ``call_kw`` refuses them.
  Before that, a Manager-role user blocked from ``ask()`` could call
  ``ncollection.ai.context.build()`` by RPC and receive **unsanitised**
  company-wide receivables.

* The tenant identity sent to the gateway is **not authenticated** — tracked in
  #373, since the fix spans the already-merged satellite (#59).

* Prompts are assembled tenant-side. ``AI_PLATFORM_DESIGN.md`` §3 specifies
  gateway-side layering with a recorded template version; not implemented here.

Usage
=====

.. code-block:: python

    # Free text is OFF by default — read "Known limits" and #375 first.
    env['ir.config_parameter'].sudo().set_param(
        'ncollection_ai.enable_free_text_questions', 'True')

    env['ncollection.ai.question'].ask("Which customer owes us the most?")

.. code-block:: bash

    make ai-up                 # start the gateway satellite (mock provider)
    make ai-context-sample     # review the context for 20 sample questions

Natural-language search domains (P5-T05)
========================================

``ncollection.ai.domain.mapper`` turns a question into a **validated Odoo search
domain** for one of four models — ``sale.order``, ``account.move``,
``stock.picking``, ``crm.lead`` — and stops there. It does not run the domain:
no ``search``, no ``read``, no write, no ``sudo``. Execution belongs to the
consumer (P5-T07 / #64), under that caller's own access rights.

**Off, and mock-only.** Two independent gates, both deliberate:

* ``ncollection_ai.enable_nl_domain_mapper`` — its own parameter, default
  **False**. Deliberately NOT ``enable_free_text_questions``: #375 treats free
  text as a separate, later decision, and one shared flag would mean enabling
  either capability silently enabled the other.
* The satellite must report the **mock** provider on ``/healthz``. #375 requires
  a zero-retention agreement *and* a recorded administrator acknowledgement
  before any live provider, and that acknowledgement mechanism does not exist
  yet — so the config flag alone cannot start sending prose to a real provider.
  This is a safety gate, not an authorisation mechanism; it does not make the
  capability production-ready.

**What crosses the boundary** is the question plus the field table for one
model — names, types, and the options a selection field accepts. No records, no
values, no aggregates. ``ncollection.ai.context`` is never called from here, so
no tenant data can reach this prompt by any route.

**Provider output is data, never code.** It is parsed with ``json.loads`` and
then checked leaf by leaf in ``models/domain_schema.py`` — the whitelist *is*
the boundary. Dotted traversal, unknown fields, traversing operators
(``child_of``, ``any``), wrong value types, malformed prefix notation, and
oversized or over-nested domains are all refused. A refusal never echoes the
provider's text back.

The field table was read off the live models (``ir_model_fields``), not
inferred: ``account.move.user_id`` is absent because it is not stored, and
``sale.order`` has no ``done`` state in Odoo 19.

Conformance set: ``data/domain_test_set.json`` — 56 valid mappings plus
adversarial cases (injection, malformed output, unsafe structure), exercised by
``tests/test_domain_mapper.py`` with the gateway patched out.

The assistant chat widget (P5-T06)
==================================

A floating OWL assistant on every backend screen (``main_components``):
minimize/expand, unread dot, per-user transcript, suggestion chips that follow
the current module, and a typing indicator. Server half is
``ncollection.ai.chat``; history is ``ncollection.ai.chat.message``.

**How it is usable while free text is OFF — and why that is not a #375
bypass.** The client sends a **key**; the question text is resolved
server-side from ``data/suggested_prompts.json`` and an unknown key is
refused. Server-owned English in a reviewable file is not *arbitrary user
prose*, which is the category #375 gates. The curated path therefore runs the
same pipeline (``_run_pipeline``: build → sanitise → send → rehydrate) without
the free-text gate — and nothing else changes: typed input is a thin wrapper
over ``ask()`` and inherits the flag, the secret scans and the cap; the input
box is not even rendered until the server reports the flag on.

Every suggestion is drawn from the **answerable** tier of the P5-T03 sample
set, because a chip whose answer is "the data does not contain this" teaches
users the assistant is broken. Grow ``suggested_prompts.json`` when the
context builder grows sections, not before.

**History is private per user, in both directions.** ACLs admit the
financial-gate groups only; a **global** record rule scopes every operation to
``user_id = user.id`` (two accountants cannot read each other); there is no
write/unlink ACL below ``base.group_system``, because an editable record of
what the assistant said is a tampering surface. An exchange is persisted only
**after** an answer exists — a refusal or a gateway failure leaves no
transcript.

**Provider output is data end to end.** Stored as ``Text`` (never ``Html``),
returned verbatim, and rendered client-side by an escape-first markdown subset
(bold, inline code, ``- `` bullets): every character is HTML-escaped before
any tag is introduced, so there is no path from model output to markup.

**Streaming (P5-T06 acceptance).** The widget streams: ``POST
/ncollection/ai/chat/stream`` (Odoo CSRF token, ``auth='user'``) relays the
gateway's ``/v1/stream`` as newline-delimited JSON; each piece is re-hydrated
incrementally (a half identity token is never shown) and rendered by the same
escape-first renderer. Admission is the SAME code as the batch RPCs and runs
before the first byte; the exchange is saved once, only after the provider's
final ``done``, so a cut-off answer leaves no transcript. A stream holds one
web worker for its duration, never the cron worker (the #310 hazard §6 names);
if the stream route is unavailable the widget falls back to the batch RPC. The
only provider proven end to end is the mock; the Anthropic SSE parser is
unit-tested on recorded frame shapes and has not run against the live API.

**Concurrency cap.** An AI request holds one web worker for the provider's whole latency (batch and streamed alike). Measured on a `--workers=2` server with a 7.7 s answer, two concurrent questions delayed an ordinary request by 6.2 s, and the workers are shared by every tenant. Each database therefore gets at most `ncollection_ai.max_concurrent_requests` AI requests in flight (default 2; a Postgres advisory lock per slot, so it holds across worker processes); the next is refused at once with a friendly message. With the cap at 1 the same experiment refused the second stream in 52 ms and the ordinary request took 25 ms. The gateway additionally charges a stream that ends early for what it produced, so cancelling after the first chunk is not free model use. Size `workers` for the number of tenants that may ask at the same time.

**Server-wide admission.** The per-database cap alone does not protect the pool: several tenants each using their own allowance can still fill every worker. A second advisory lock, taken in the server's maintenance database (the one place all tenants share), caps AI requests across ALL databases at `ncollection_ai.max_concurrent_global`; unset, it is half of Odoo's `workers` and never more than `workers - 1` (threaded mode: 2). A request waits at most `ncollection_ai.slot_wait_ms` (default 300, maximum 2000) for a slot and is then refused with a friendly message; if the maintenance database cannot be reached the request is refused, never run unbounded. Slots are released in a `finally`, so a cancelled or abandoned stream gives its slot back. Measured with three tenants streaming at once on `--workers=3`: one stream admitted, the other two refused in 379 and 790 ms, ordinary requests on every tenant 19 ms (idle 18 to 25 ms), and a new stream admitted afterwards (`e2e/rehearsal/multi_tenant_probe.js`).

**Transcript integrity and size.** Users hold READ access only; the service
writes both rows with ``sudo()`` and an explicit ``user_id`` from the
authenticated request, so nobody can plant an ``assistant`` row through the
ORM or RPC. A user row is capped at the question cap (2000 characters), an
assistant row at 20000; the service truncates and a model constraint refuses
anything larger.

**Retention.** A daily cron deletes transcript rows older than
``ncollection_ai.transcript_retention_days`` (default 90; ``0`` or less keeps
them forever). The parameter and the cron are seeded ``noupdate``, so a tenant's
tuning survives upgrades.

**Language.** The transcript shows the suggestion's label in the viewer's
language; the provider still receives the registry's English question (what a
reviewer can read in the JSON file) inside a prompt that tells it to answer in
the viewer's language. Whether a real model obeys that instruction is not
provable offline.

**Remaining limits:**

* **Module context is best-effort.** The widget reads the active model from
  the router (record pages); anywhere it cannot, the server's ``default``
  prompt set applies. The server treats ``module_ctx`` as untrusted either
  way.
* **A transcript is a convenience, not evidence.** It is deleted after the
  retention window and is not an audit record (earlier versions let a user
  plant their own rows; that path is closed, but the log is still not a
  compliance artefact).

Languages and the browser proof
-------------------------------

The widget ships English and Arabic (RTL). The suggestion chips come from
``data/suggested_prompts.json``, which Odoo's translation export cannot read,
so each label is ALSO written as a ``self.env._()`` literal in
``models/chat.py`` (``_chip_labels``) and translated in ``i18n/ar.po``. To add a
prompt: add it to the JSON, add its label to ``_chip_labels``, add the Arabic
msgstr; ``test_every_chip_label_is_translatable`` and
``test_every_chip_label_has_an_arabic_translation_on_file`` fail until all three
agree. Only the label is translated: the question text sent to the provider
(and echoed in the transcript) is the registry's English text, by design.
JavaScript ``_t()`` terms are not picked up by the export in this repo, so they
are kept in ``ar.po`` by hand (look for ``odoo-javascript`` entries for
``chat_widget.js``).

``e2e/ai/acceptance_chat.js`` is the browser acceptance (13 checks: gate,
suggestion round trip, reload, renderer XSS by parsing the output into the
DOM, English and Arabic at desktop and 390 px, isolation, free text on/off).
``e2e/ai/fixtures/run_ai_web.sh`` builds a fully private stack for it (own
Postgres, the mock-provider gateway satellite, Odoo from the working tree; the
gateway key is generated per run, kept in the environment only, and the stack is
removed with ``run_ai_web.sh clean``).

Credits
=======

NCollection — P5-T03, P5-T05 and P5-T06, building on P4-T01 (aggregation
engine) and P5-T02 (gateway satellite).
