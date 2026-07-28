# UAE Tax-Invoice Requirements Checklist (P3-T09)

Maps the UAE **FTA full tax-invoice** requirements — Cabinet Decision No. 52 of
2017 (the VAT Executive Regulation), **Article 59(1)**, under Federal
Decree-Law No. 8 of 2017 — to what produces each element on the invoice this
module renders, plus the test that proves it.

The layout itself is **Odoo-owned** (`account.report_invoice_document` →
`l10n_ae`'s `l10n_ae_report_invoice_document`, a primary inherit of
`l10n_gcc_invoice`'s bilingual GCC tax invoice). This module only **applies the
`'ae'` localization** (P3-T04), **enables the bilingual layout**, and adds the
**tenant brand accent** (P1-T16, invoice-scoped). Nothing about the FTA layout
is re-authored here.

Sample invoice under test: a posted `out_invoice`, supplier + a **VAT-registered
(company) recipient** both with a 15-digit TRN, one line at AED 100 + 5% VAT —
rendered via `ir.actions.report._render_qweb_html('account.account_invoices', …)`
in `tests/test_uae_invoice.py`. The registered recipient is what makes l10n_ae
render the **full** tax invoice (`_l10n_ae_is_simplified` is false for a company
recipient), not the simplified one — the test asserts `"Simplified"` is absent.

| # | FTA requirement (Art. 59(1)) | Satisfied by | Evidence |
|---|------------------------------|--------------|----------|
| 1 | The words **"Tax Invoice"** clearly displayed | `l10n_ae`/`l10n_gcc` report title | `test_invoice_pdf_has_uae_fta_elements` asserts `"Tax Invoice"` |
| 2 | Supplier **name, address and TRN** | `web.external_layout` company header + `l10n_ae` TRN block (`company.vat`) | same test asserts `company.vat` (TRN) renders |
| 3 | Recipient **name, address and TRN** (where registered) | partner block; `l10n_gcc` renders the customer TRN | `test_invoice_pdf_has_uae_fta_elements` asserts `invoice.partner_id.vat` (recipient TRN) renders |
| 4 | **Sequential number** uniquely identifying the document | `account.move.name` (Odoo journal sequence) | same test asserts `invoice.name` renders |
| 5 | **Date of issue** | `invoice_date` in the info block | rendered in `#informations` |
| 6 | **Date of supply**, if different | `l10n_gcc` "Delivery Date" field | field present in the GCC layout |
| 7 | **Description** of goods/services | invoice line `name` | line table |
| 8 | Per line: **unit price, quantity, tax rate, line amount** (AED) | invoice line columns (price/qty/taxes/subtotal) | line table; currency = AED (from the `'ae'` chart) |
| 9 | **Gross amount payable** in AED | `amount_total`, company currency AED | totals block |
| 10 | **Discount** amount, if any | line discount column | line table |
| 11 | **Total VAT charged**, in AED | `tax_totals` VAT summary | same test asserts `"VAT"` renders |
| 12 | **Bilingual** Arabic/English layout | `l10n_gcc` dual-language layout (flag + Arabic active, enabled by localization) | `test_invoice_pdf_is_bilingual` asserts Arabic script renders |
| — | Tenant **brand** on the document (P1-T16) | logo via `web.external_layout`; primary-colour rule via `views/report_invoice.xml` | `test_invoice_pdf_carries_tenant_brand_colour` asserts the hex renders |

## How the bilingual layout is switched on

The GCC layout renders its Arabic column only when **both** hold: the company's
`l10n_gcc_dual_language_invoice` flag is set **and** Arabic (`ar_001`) is active.

- `res.company._nc_apply_uae_localization()` calls `_nc_enable_bilingual_invoices()`
  on each company it localizes, which sets the **flag** (idempotent, fail-soft).
  For **existing** tenants, `migrations/19.0.1.2.0/post-migrate.py` sets it on
  upgrade (post-init hooks don't fire on `-u`).
- **Arabic** is already active: `l10n_gcc_invoice` (a hard transitive dependency,
  installed before us) activates `ar_001` globally in its own `post_init` hook,
  so we don't re-activate it here. Broader Arabic/RTL UI enablement is **P3-T08**.

## Deliberately out of scope (documented gaps)

- **Foreign-currency → AED conversion line** (Art. 59(1)(i)): required only when
  the invoice is in a non-AED currency. Odoo's multi-currency prints the
  company-currency tax total; the full per-invoice AED-conversion presentation
  lands with **AED / multi-currency, #46 (P3-T06)**.
- **Reverse-charge statement** (Art. 59, RC supplies): handled at posting via the
  `'ae'` GCC/international **fiscal positions** (no VAT charged); an explicit
  printed "recipient must account for the tax" note is **not** added here —
  file a follow-up if a customer requires the printed statement.
- **Simplified tax invoice** (Art. 59(5), supplies < AED 10,000 / retail): the
  full tax invoice above is a superset; a dedicated simplified/thermal format is
  **not** in this ticket.
- **E-invoicing QR / structured e-invoice**: the UAE e-invoicing mandate spec is
  not GA — deferred (no ticket yet).
