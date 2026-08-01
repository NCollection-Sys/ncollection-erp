==============================
NCollection Data Import Toolkit
==============================

**Task:** P3-T11 · **Layer:** tenant ERP (never platform)

Onboarding import toolkit for a **fresh tenant**: starter CSV templates + a
guided wizard so a non-technical admin can load a company's opening data —
**customers, suppliers, products, opening stock, opening balances** — *without
developer help* (the ticket's acceptance).

Reuse-first (Rule 2/5): Odoo owns importing
============================================

Odoo 19 Community already solves every hard part, so this module **adds no import
engine and no OCA dependency** — it only adds the onboarding *layer* Odoo lacks:

- **``base_import``** (core) — CSV/XLSX column mapping, a dry-run "Test", external-ID
  create-or-update, and per-row errors. Customers/suppliers (``res.partner``) and
  products (``product.template``) import through it directly.
- **``account.account.opening_debit`` / ``opening_credit``** — writing these feeds a
  single company opening move and **auto-plugs any Dr≠Cr into Current-Year-Earnings**,
  so an opening-balances CSV *cannot* produce an unbalanced entry.
- **``stock.quant.inventory_quantity``** (inventory mode) — an opening-stock CSV records
  the counted quantity; the native **Apply** turns it into on-hand with a balancing move.

``base_import_async`` (OCA) was surveyed and **deferred**: AGPL, and it would push
``queue_job`` into every tenant DB — an architecture change the onboarding-scale
case doesn't need.

What this module ships
======================

- **Starter templates** (``data/templates/*.csv``) — headers are the exact Odoo
  import field names, so they map with no fiddling; 1–2 sample rows each.
- **Onboarding wizard** (``ncollection.data.import.onboarding``) — *Settings → Data
  Import → Import Tenant Data*: download a template, open the native importer for
  each data set in the right order, and **validate a filled-in file** (a dry-run
  that rephrases ``base_import``'s row errors in plain language — the acceptance's
  "validation a non-technical admin can understand").

Recommended order (dependencies first)
======================================

**Products → Customers → Vendors → Opening Stock → Opening Balances.** Opening
stock references products; opening balances reference the Chart of Accounts (so
run after P3-T05 seeds the CoA — the ticket's dependency).

Two native follow-through steps (documented, not hidden)
========================================================

- **Opening stock:** after importing, click **Apply** in Physical Inventory to move
  the counted quantities to on-hand (Odoo's own two-step count → apply).
- **Opening balances:** the update targets existing accounts, so **export your Chart
  of Accounts first** (that adds the External ID column), fill ``opening_debit`` /
  ``opening_credit``, and re-import. The shipped ``opening_balances.csv`` shows the
  columns; the ``id`` placeholder is replaced from your export.

Boundaries
==========

- No custom import engine, no bespoke per-entity wizards — Odoo core does the work.
- No new dependency, no ``repos.yml`` change.
- Tenant-side only (``res.partner`` / ``product.*`` / ``stock.quant`` /
  ``account.account``) — no platform (``ncollection_saas``) coupling (Rule 3).
- Not auto-installed fleet-wide: it's a plan-gated tenant module (accounting +
  inventory tenants), not part of ``CORE_TENANT_MODULES``.
