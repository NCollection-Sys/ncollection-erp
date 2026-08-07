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
  An earlier version of this section drew the line at "declared vs undeclared"
  secrets. That was an overclaim — ``"the CVV is 123"`` is declared in plain
  English and was leaking, because ``cvv`` was simply missing from the noun
  list. The undecidability argument is real, but it was being used to cover a
  gap it did not apply to. The actual boundary:

  **Caught** — a credential named by a noun in ``_CREDENTIAL_NOUN`` and either
  immediately followed by a letters-and-digits token (``"wifi password
  p4ssW0rd2026"``) or joined within ~40 characters by a connector
  (``is``/``are``/``was``/``were``/``to``/``=``/``:``/``,``/`` - ``) whose value
  is not an ordinary state word; **or** any single token over 19 characters
  mixing letters and digits; **or** anything matching a structural shape
  (vendor prefixes, JWT, PEM, connection string, IBAN, card PAN). Env-var and
  config shapes (``DB_PASSWORD=``, ``wifi_password:``) are included — they were
  not, until round 6, because ``\b`` does not fire between ``_`` and a letter.

  **Not caught**, each demonstrated by a reviewer and each tracked in the
  corpus so the count stays visible:

  - a noun outside the list (``"the doorcode is 4521"``)
  - no noun at all (``"check correcthorsebatterystaple for me"``)
  - a decoy clause that puts a state word in the value slot and the real secret
    further along (``"the password is not accepted, it's actually p4ss123"``).
    Closing this needs a scan of every token near the noun, which was measured
    and rejected: it refuses ``"the api key rotation policy: how many keys are
    older than 90 days?"`` because *rotation* is eight characters.
  - a declaration whose connector falls outside the window

  The noun list is maintainable and should grow whenever a gap is found. The
  rest is genuinely undecidable — a lowercase passphrase is indistinguishable
  from prose — and is why this feature needs a zero-retention provider
  agreement rather than a cleverer pattern.

  **Six review rounds established this.** Every round that tried to close the
  gap with more pattern opened a false refusal elsewhere, and a control that
  blocks *"Can you pass this invoice to Sarah?"* gets switched off — a worse
  security outcome than the gap it closed.
* **Only ``ask()`` is public.** ``_build``, ``_sanitise``, ``_rehydrate`` and
  ``_complete`` are underscore-prefixed so Odoo's ``call_kw`` refuses them
  outright. Before that, a Manager-role user blocked from ``ask()`` could call
  ``ncollection.ai.context.build()`` by RPC and receive **unsanitised**
  company-wide receivables — sanitisation only happens inside ``ask()``.
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
