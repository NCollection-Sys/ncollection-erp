=========================
NCollection UAE E-Invoicing
=========================

The UAE e-invoicing engine (PINT-AE) for NCollection ERP: customer invoices and credit notes are mapped from the
posted accounting document, reconciled against the official rules, serialised to PINT-AE XML, validated by the
official Schematron in an isolated worker, and archived immutably. Provider-agnostic: no Accredited Service Provider
is built in.

This is a gated module: it is available only to tenants that are verified UAE tenants with Accounting, and it is
activated only by an explicit per-tenant decision. See docs/markdown/UAE_EINVOICING.md.
