=============================================
NCollection UAE E-Invoicing: A-Cube adapter
=============================================

The Accredited Service Provider (ASP) adapter for A-Cube (MoF accreditation 188961), behind the provider-neutral
adapter contract of ``ncollection_uae_einvoicing``. The engine never imports this module; it only finds the adapter
through the contract's registry.

Built against A-Cube's PUBLISHED Gov AE OpenAPI contract. It is **not yet proven against the provider sandbox** (no
credential), so it is not for production. Every test runs against a scripted server built from that contract; none
reaches the provider.

What a sandbox run still has to prove (UAE_EINVOICING.md §9f): that A-Cube keeps the document's own ``cbc:UUID``,
the submission and status flow end to end, and the callback behaviour (A-Cube webhooks carry a static token, so a
callback is only a trigger and the state is re-read from the API).

See docs/markdown/UAE_EINVOICING.md §9f and docs/markdown/UAE_EINVOICING_ACUBE_SANDBOX.md.
