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

* **Free-text secret detection is best-effort and cannot be made complete.**
  Deciding whether a run of prose is a secret is undecidable —
  ``correcthorsebatterystaple`` is a passphrase and also five English words.
  Three triggers are applied (high-confidence structured shapes; a credential
  noun handed a value; a credential noun co-occurring with an unusually long
  token), and together they refuse every case four review rounds produced. An
  **undeclared** secret shaped like ordinary prose — ``"check
  correcthorsebatterystaple for me"`` — still reaches the provider. This is
  inherent, not a defect awaiting a fix, and it is why the feature needs a
  zero-retention provider agreement rather than a cleverer pattern.
* Long hex strings are redacted, so a question about a **checksum** gets
  ``[REDACTED]``. Deliberate: no shape separates a SHA-1 digest from a 40-char
  HMAC key, and §5's "never send" for secrets is unconditional.
* The free-text identity scan is bounded (``_IDENTITY_SCAN_LIMIT``). A partner
  outside that window is not pseudonymised in free text. The structured path
  remains the primary control.
* The tenant identity sent to the gateway is **not authenticated** — tracked
  separately, since the fix spans the already-merged satellite (#59).
* Prompts are assembled tenant-side. ``AI_PLATFORM_DESIGN.md`` §3 specifies
  gateway-side layering with a recorded template version; that is not
  implemented here and is tracked separately.

Usage
=====

.. code-block:: python

    env['ncollection.ai.question'].ask("Which customer owes us the most?")

.. code-block:: bash

    make ai-up                 # start the gateway satellite (mock provider)
    make ai-context-sample     # review the context for 20 sample questions

Credits
=======

NCollection — P5-T03, building on P4-T01 (aggregation engine) and P5-T02
(gateway satellite).
