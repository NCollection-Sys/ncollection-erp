# Paid / Enterprise-style functionality — Community / OCA gap audit

Verified 2026-10-12 (OCA scout; `git ls-remote`, blobless clones and a throwaway `odoo:19` container). "19.0?" means a manifest on the
OCA 19.0 branch was seen. Coverage % is an estimate against the Enterprise feature. **Nothing here is installed or pinned**; every
"ADOPT" needs the owner's approval (CLAUDE.md Architecture Safety) and a `repos.yml` + `addons_path` change in 3 places
(`config/odoo.conf`, `config/odoo.prod.conf`, `.github/workflows/ci.yml`).

| Capability | Community native | OCA module (19.0?) | Cov. | Missing | NCollection overlap | Recommendation |
|---|---|---|---|---|---|---|
| Accounting reports | none (renderer is Enterprise) | `account_financial_report` yes (pinned) | 90 | — | native `ncollection_account_reports` | REUSE native |
| Bank reconciliation | basic statements + recon models | `account_reconcile_oca` yes `19.0.1.0.12` | 75 | online sync | none; D1 §4.4 names `OCA/account-reconcile` for P10-T06 | ADOPT (new repo, doc-sanctioned) |
| Bank statement import | none | `account_statement_import_*` (CSV/XLSX/CAMT/QIF) yes; OFX, MT940 no | 65 | OFX, MT940 | none | ADOPT with reconcile (new repo, needs approval) |
| Asset management | none | `account_asset_management` yes (pinned) | 85 | — | `ncollection_account_assets` | REUSE |
| Budget | none | `account_budget_oca` yes | n/a | single analytic FK (#121) | native `ncollection_account_budget` | REUSE native |
| Deferred revenue / expense | **none** | `account_cutoff_*` **not** on 19.0 | 15 | amortisation entries | none | BUILD or defer |
| Recurring billing | none | `contract` yes (Prod/Stable) | 70 | usage billing, dunning | `ncollection_billing` is platform billing only | ADOPT only if a tenant need arises |
| Fiscal-year lock / closing | **native** lock dates + exceptions | `account_lock_date_update` yes (pinned repo); `account_fiscal_year_closing` yes (new repo) | 85 | year-end closing entries | none | REUSE native |
| Payment follow-up / dunning | none | `account_credit_control` yes | 70 | letters polish | P2-T14 dunning is platform subscriptions | ADOPT (new repo) if tenant AR chasing is wanted |
| E-signature | none | `sign_oca` yes | 55 | audit trail | none | low priority |
| Approvals | none | `base_tier_validation` **not** on 19.0 | n/a | — | native `ncollection_approvals` | REUSE native |
| Helpdesk | none | `helpdesk_mgmt` (pinned); **`helpdesk_mgmt_sla` now on 19.0** | 90 | — | native `ncollection_helpdesk` | **Sunset trigger fired**: bump the helpdesk pin on a branch, compare, retire the native SLA module |
| Field service | none | `fieldservice` yes (+~35 add-ons) | 70 | mobile app | none | ADOPT only if the roadmap adds FSM |
| Planning / shifts | none | `shift-planning` 19.0 **empty** | 15 | Gantt shifts | none | BUILD, low priority |
| Documents / DMS | none | `dms` yes (LGPL-3) | 70 | OCR, workflow | none | ADOPT-able |
| Knowledge | none | `document_page` yes | 50 | block editor | none | optional |
| Quality control | none | `quality_control_oca` (base only) | 30 | stock/MRP glue | none | BUILD or wait |
| PLM | none | not found | 10 | ECO, versions | none | out of scope |
| **Barcode / WMS** | `barcodes` + GS1 only | `stock_barcodes` **not on 19.0** (PR #742 open); shopfloor empty | 20 | scan workflow | P7-T04/T07 | **BUILT:** `ncollection_stock_barcode` (see APPLICATIONS_AND_BARCODE.md) |
| Studio-like customisation | custom fields, `base_automation` | none | 20 | visual builder | `ncollection_core` workspace config | out of scope |
| Mobile app | none | `web_responsive`, `web_pwa_customize` yes | 40 | native app | roadmap P7-T05 | per roadmap |
| VoIP | none | `voip_oca` yes | 50 | softphone polish | none | optional |
| Appraisal | none | not in OCA/hr 19.0 | 0 | all | none | BUILD, low priority |
| Payroll | none | `payroll` yes (LGPL-3) | 40 | UAE rules, WPS/SIF | none | ADOPT-able engine + a UAE localisation build |
| Marketing automation | `mass_mailing`, `base_automation` | none | 30 | journeys | none | BUILD or skip |
| E-commerce | **`website_sale` native** | `OCA/e-commerce` yes | 90 | — | platform is backend/portal-only | out of scope |
| Rental / Social marketing | none | none | 0–5 | all | none | skip |
| Consolidation | multi-company native | `account-consolidation` 19.0 **empty** | 30 | engine | P10-T06 | BUILD (P10-T06) |

Verified **absent** from the `odoo:19` image (so each is a real gap): `account_asset`, `account_budget`, `account_reports`,
`account_accountant`, follow-up/dunning, `sign`, `documents`, `knowledge`, `approvals`, `helpdesk`, payroll, `planning`, FSM,
quality, PLM, `voip`, appraisal, `studio`, consolidation, `stock_barcode`, subscription, rental. Present natively: `barcodes`,
`barcodes_gs1_nomenclature`, `stock_picking_batch`, lock dates, `website_sale`, `mass_mailing`, `base_automation`, `hr_attendance`.

Top actions: (1) do **not** pin `stock-logistics-barcode`; (2) if bank reconciliation is next, adopt `OCA/account-reconcile` +
`OCA/bank-statement-import` with approval; (3) re-evaluate `ncollection_helpdesk` against `helpdesk_mgmt_sla`.
