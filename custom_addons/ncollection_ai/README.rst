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
  on. See issue #375, which P5-T06 must resolve first.

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

Credits
=======

NCollection — P5-T03 and P5-T05, building on P4-T01 (aggregation engine) and
P5-T02 (gateway satellite).
