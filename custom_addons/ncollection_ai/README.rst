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
``sk_``/``AKIA``/``ghp_`` prefixes, ``Bearer`` headers), IBANs, and
government-issued identifiers are redacted wherever they appear; known partner
and company names are pseudonymised.

The second tier exists because the module's own entry point, ``ask(question)``,
takes free text. Four reviewers independently demonstrated that a customer name,
an API key, a JWT and a passport number all reached the provider verbatim when
only the first tier existed.

Known limits
============

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
