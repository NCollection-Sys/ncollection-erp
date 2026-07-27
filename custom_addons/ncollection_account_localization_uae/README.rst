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

Deferred (not in this ticket)
=============================

- **``l10n_ae`` dependency + UAE VAT/CoA/AED/invoice content** → #44/#45/#46/#49.
  The scaffold deliberately does **not** depend on ``l10n_ae``; the ticket that
  configures taxes (#44) decides how to consume it, keeping this footprint
  matched to its deliverables.
- Arabic reports/documents → later localization work.

Dependencies
============

``account`` · ``ncollection_account_core`` (the financial base + shared mixin,
F1-T01) · ``base_vat`` (Odoo's VAT-check dispatch, which ``check_vat_ae``
extends). All Odoo-core or already-present — no new OCA dependency.
