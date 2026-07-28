============================
NCollection UAE Localization
============================

**Task:** F5-T01 · **Architecture:** ``FINANCIAL_PLATFORM_ARCHITECTURE.md`` §7

The UAE localization module for the NCollection financial platform. It is the
**home** the UAE deliverables land inside — UAE VAT (#44), chart of accounts
(#45), AED/multi-currency (#46), bilingual FTA invoices (#49) — and follows the
family rule: **data/templates are ours, the mechanisms stay Odoo-owned** (Odoo
owns posting, journals, taxes; ``l10n_ae`` owns the UAE CoA/tax templates).

This ticket (**#126**) ships only the scaffold slice: **TRN validation** and the
**FTA compliance checklist**.

TRN validation
==============

The UAE Tax Registration Number (TRN) is a 15-digit numeric identifier issued by
the Federal Tax Authority (no published checksum). Validation is added by
defining ``check_vat_ae`` on ``res.partner`` — plugging into Odoo's own
``base_vat`` dispatch (``_check_vat_number`` → ``check_vat_<cc>``), exactly how
core validates Saudi Arabia via ``check_vat_sa``. We **extend** Odoo's VAT-check
mechanism, we do not add a parallel one.

Because ``res.company.vat`` is ``related='partner_id.vat'``, a company's TRN is
validated through the same partner path — so the check covers **partner and
company**.

FTA compliance checklist
========================

``ncollection.fta.compliance.item`` — a **per-company** status model tracking UAE
FTA readiness. Each company is seeded with the standard requirements (TRN
registered, VAT period configured, VAT return schedule, invoice format
compliant, e-invoicing readiness); ``res.company.fta_readiness`` rolls them up
into a percentage (excluding items marked *not applicable*). Seeding is
idempotent — existing companies at install (``post_init_hook``), new companies on
create. The downstream tickets flip these rows to *done* as they land.

This is bespoke NCollection **business-layer** tracking — no Odoo/OCA module
exists for it (oca-scout survey on #126). It is not an accounting mechanism.

UAE VAT (P3-T04)
================

VAT is set up by **reusing Odoo's official ``l10n_ae`` chart template** — the
mechanism stays Odoo-owned (FPA §7). ``res.company._nc_apply_uae_localization``
calls ``account.chart.template.try_loading('ae', company)``, which installs the
UAE chart of accounts, the 5% standard / 0% zero-rated / exempt taxes + tax
groups, the domestic/GCC/international fiscal positions, and wires the company's
default sale/purchase taxes.

The ``post_init_hook`` applies it to each company that has no *real* chart of
accounts yet, so a **fresh tenant is set up unattended during provisioning**.

Two Odoo-loading subtleties are handled explicitly (both would otherwise leave
the tenant on the generic 15% chart instead of UAE 5% VAT):

- Odoo's ``account`` install schedules a **deferred** ``try_loading('generic_coa')``
  (``registry._auto_install_template``) that fires *after* every ``post_init``
  hook — it would unlink our ``'ae'`` chart and replace it. We **cancel** that
  pending fallback right after loading ``'ae'``.
- A company already left on Odoo's ``'generic_coa'`` placeholder (e.g. an
  account-first staged install) is **not** treated as "localized" — we still
  upgrade it to ``'ae'``.

It is **idempotent** (a company already on a real non-generic chart is left
alone) and **fail-soft** (wrapped in a savepoint, so a mid-load failure rolls
back and stays retryable — it logs and never breaks install/provisioning).

Acceptance proven by ``tests/test_uae_vat.py`` against the **default company**
(the one a fresh tenant actually uses — a regression guard for the deferred-race
above): it lands on the ``'ae'`` chart with 5% VAT, and a posted sale credits 5%
VAT to a real tax account.

CoA verification + the full invoice→payment→reconciliation cycle are delivered
by **#45 (P3-T05)**, below; this ticket delivers the VAT setup.

UAE Chart of Accounts (P3-T05)
==============================

The UAE CoA is Odoo's official ``l10n_ae`` chart (installed by the ``'ae'``
template applied in P3-T04) — the CoA *mechanism* stays Odoo-owned (FPA §6), so
this ticket **reuses and verifies** it rather than hand-authoring a bespoke
chart. ``tests/test_uae_coa.py`` asserts the CoA is present (receivable/payable,
income and expense accounts); separately, that the 5% VAT is linked to its VAT
(output) account; and proves the acceptance end-to-end: a
``customer invoice (5% VAT) → post → register payment → reconcile`` cycle where
revenue, VAT output, the receivable and the payment's liquidity leg each post to
the correct account and the receivable reconciles to a zero residual.

UAE tax invoice (P3-T09)
========================

The UAE FTA tax invoice — bilingual Arabic/English, TRN and per-line VAT — is
Odoo's own ``l10n_ae`` document (a primary inherit of ``l10n_gcc_invoice``'s GCC
tax invoice); the layout *mechanism* stays Odoo-owned. This ticket makes it
render for NCollection tenants and adds the tenant brand:

- ``res.company._nc_enable_bilingual_invoices`` (called from
  ``_nc_apply_uae_localization``) flips on ``l10n_gcc_dual_language_invoice`` and
  activates Arabic (``ar_001``) — the two conditions the GCC layout needs to
  render its Arabic column — so a UAE tenant's invoices are **bilingual out of
  the box**. Fail-soft (a missing field/language never breaks provisioning).
- ``views/report_invoice.xml`` adds the tenant **brand accent** (a thin rule in
  ``res.company.nc_primary_color``) above the invoice info block — the "colours"
  half of the P1-T16 branding integration, scoped to UAE invoices. The supplier
  **logo** already renders via ``web.external_layout``. ``nc_primary_color`` is a
  validated hex (``ncollection_branding._check_nc_brand_colors``) — no injection
  surface in the style interpolation.

The full FTA requirement-by-requirement mapping (Cabinet Decision 52 of 2017,
Art. 59) with the test proving each element is in ``UAE_TAX_INVOICE_CHECKLIST.md``.
Acceptance is proven by ``tests/test_uae_invoice.py``: the rendered document
carries the ``"Tax Invoice"`` title, supplier TRN, per-line VAT and sequential
number; renders Arabic (bilingual); and carries the tenant brand colour.

Broader Arabic/RTL UI enablement is **P3-T08**; per-invoice foreign-currency →
AED conversion lands with multi-currency (**#46**). See the checklist's
"out of scope" for the documented gaps (RC statement, simplified/thermal, QR).

Deferred (not in this ticket)
=============================

- **AED / multi-currency** → #46 (P3-T06).
- Arabic/RTL UI enablement → #48 (P3-T08); e-invoicing QR → later.

Dependencies
============

``account`` · ``ncollection_account_core`` (the financial base + shared mixin,
F1-T01) · ``base_vat`` (Odoo's VAT-check dispatch, which ``check_vat_ae``
extends) · ``l10n_ae`` (Odoo's official UAE chart template — the VAT/CoA
mechanism P3-T04 applies). All Odoo-core or already-present — no new OCA
dependency.
