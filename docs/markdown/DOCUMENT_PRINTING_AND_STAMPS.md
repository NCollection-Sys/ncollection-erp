# Document printing, View and stamps

One printing framework: Odoo's report engine. NCollection adds a layout, owns the View endpoint and overlays stamps; it does not add
a second renderer.

## Pipeline (what happens when you press Print or View)

1. A document model mixes in `nc.document.print.mixin` (`ncollection_core`): `_nc_print_report()` (which report) and
   `_nc_print_context()` (its context). **Print and View both call exactly these**, so the report, template, context, data and page count are
   the same by construction.
2. `ir.actions.report._render_qweb_pdf` renders QWeb to HTML, wkhtmltopdf (inside the odoo container) turns it into a PDF using the
   company layout and the stylesheet it fetches from `web.base.url`.
3. `ncollection_branding` overrides `_render_qweb_pdf_prepare_streams` and overlays the stamp on each record's own stream (skipping
   streams reused from a stored attachment), so the stamped PDF is what View, Print, email and stored copies all return.
4. **Print** = the report action (download). **View** = `GET /ncollection/document/view/<model>/<id>` (`ncollection_core`): the same render,
   `Content-Disposition: inline; filename=<document>.pdf`, `no-store`, `nosniff`, 404 for anything that is not printable, 403 on no access;
   it opens in a new tab with the browser's PDF viewer. No HTML preview, no portal page, no second template.

## Layout and design

`ncollection_branding`: `report.layout` "Professional" (default for companies that never chose a layout; a deliberate choice of
another layout is kept), `external_layout_nc`: logo or name, company details and VAT/TRN, a rule in the company colour (validated hex
only), footer text and *Page X / Y*. LTR/RTL follows the document language. Fonts are the bundled Lato and Tajawal. The body tables,
totals, status colours and signature lines are shared CSS (`nc_document_css`); wkhtmltopdf's WebKit has no CSS variables or flexbox, so
none are used. The stock RTL stylesheet draws black table borders; the layout resets them first.

The letterhead's logo is the **company's** logo, or its name when it has none. The vendor marks a database is born with (Odoo's default
company logo, and the NCollection mark this module seeded until 19.0.1.15.0) are removed on install and on every upgrade, exact bytes
only (`res.company._nc_clear_vendor_logos`): printed on an invoice they read as the company's own. An uploaded logo is never touched.

## Print Settings (one per kind of printed document)

The Print Settings of a document live on its **document type** (`ncollection.document.type`, shipped as data by the module that prints it:
accounting, sales, purchase, reports). There is no second template system and no HTML editor: a setting chooses among what the templates
already render, and survives upgrades because it is a field on a data record, not a copy of a template.

* **Paper format** (`paperformat_id`). `ir.actions.report.get_paperformat` (`ncollection_branding/models/ir_actions_report.py`) resolves,
  in order: the report's own paper (a cheque declares its exact size), the document type's Print Setting, the **A4 Document (letterhead)**
  paper when the company prints with the NCollection letterhead (Odoo's stock A4 reserves ~40 mm for a boxed header, which left a blank
  band above this ~25 mm one), then the company's paper. Preview and Print both resolve through here. Kinds printed by the same report
  (invoice, bill, credit note) share one paper, so choosing it for one chooses it for all of them.
* **Optional sections**: a module adds its switch to the document type and to `_nc_print_setting_fields()`. Accounting adds
  **Payment status** (paid amount + status under the totals, `nc_show_payment_status`).
* **Who may change them**: the model decides (`_nc_check_print_settings_allowed`): only the print-setting fields, and only by a Settings
  administrator or a member of the kind's `manager_group_ids` (the Accounting Adviser for the accounting kinds), the same rule as stamps.
  Everyone else reads.
* **Where**: Workspace Administration > Print Settings (every kind), Accounting > Configuration > Print Settings (the accounting kinds).

The financial reports keep their own report-specific papers (`ncollection_account_reports/report/nc_print.xml`: portrait/landscape) and
their own print payload; they are listed as a document type for stamps and can be given a paper here too.

The **Payment Receipt** (`ncollection_account_core/report/payment_receipt.xml`) shows payment information, party, date, method,
journal, status, the amount (and in words), an *Applied to* table built from the real reconciliations (document, date, total, applied,
balance due) and totals: applied, not applied (credit on account), payment amount. Nothing is invented: an unconfirmed or unallocated
payment says so. The accounting and reconciliation engine is not touched.

## Print Templates (how each printed document looks)

**Where:** Workspace Administration > **Print Settings** (every kind of document) and Accounting > Configuration > **Print
Settings** (the accounting kinds). Both open the list **Print Templates**; a row opens the document's template form, and
**Preview** (list button or form header) opens the latest real document of that kind.

**What can be set** (fields of `ncollection.document.type`, `ncollection_branding/models/document_print_template.py`):

| Tab | Settings |
|---|---|
| Template | document title (replaces the standard title, the number follows), logo (company / system / the document's own / none), company details on or off, header text, footer text (replaces the company report footer), page numbers |
| Colours and typography | accent colour, text colour (validated hex), font (a fixed list the PDF engine has), text size 6-16 pt |
| Page | paper size, orientation, four margins: together they become the kind's own `report.paperformat` ("Print template: <kind>"), kept in step on every change |
| Sections and columns | customer address block, payment reference, document stamp, quantity / unit price / discount / taxes columns; accounting adds payment status |
| Language | bilingual labels (where the template provides Arabic and English) or the document language only; direction automatic / LTR / RTL |

**Supported documents:** every document type shipped as data: customer invoice, customer credit note, vendor bill, vendor
credit note, payment receipt, quotation, sales order, RFQ, purchase order and the financial reports. A new printable document
plugs in by shipping a document type record for its report: no new screen, no new code path.

**One renderer.** `external_layout_nc` asks `_nc_print_options(record)` for the template of the document it prints; validated
values only reach its `<style>` block (colours, font stacks, known cell names). `get_paperformat` asks for the page. Preview,
View and Print are the same `_render_qweb_pdf` call on the same report, so a saved setting shows in all three. Presentation only:
no amount, tax or residual is computed here. This is not Odoo Studio: there is no template editor and no copied view, so an
upgrade of Odoo's own templates is never overwritten.

## Stamps

`ncollection.document.stamp` (admin: Workspace Administration > Document Stamps): name, company, PNG image (transparency kept), active,
**priority**, **document types** (`ncollection.document.type`: customer invoice, credit note, vendor bill, vendor credit note, payment
receipt, sales quotation / order, RFQ, purchase order, financial report), optional **paper formats**, page scope (first / last / all),
position and size in **millimetres** from the top-left of the paper, keep-proportions.

* Selection is deterministic: the record's company, active, the document type of the record, paper format (a stamp naming the paper beats one
  that names none), higher priority, then the older stamp. Exactly one stamp is drawn. A kind with no stamp is not stamped; the company's
  legacy single stamp image remains the default for registered documents that no template covers.
* Placement is exact for any paper size / orientation / custom size: the overlay is drawn with reportlab in PDF points and merged
  (`models/stamp_overlay.py`); tests read the `cm` matrix back out of the finished PDF and assert the millimetres.
* The designer widget (OWL, `static/src/stamp_designer`) shows the paper to scale, lets you drag and resize, nudge, enlarge / reduce,
  keeps the aspect ratio, shows the physical size, and previews through a **real PDF** (`/ncollection/stamp/preview/<id>`: the newest real
  document of the stamp's type, else a labelled sample sheet) and *Print test*.
* Tenant and company isolation: a stamp is a company record under a record rule; the preview route 404s for anything the user cannot read.

### Where it is configured, and who can read the image

* **Workspace Administration > Document Stamps** (all kinds) and **Accounting > Configuration > Document Stamps** (the five accounting kinds:
  customer invoice, customer credit note, vendor bill, vendor credit note, payment receipt). Both open the SAME model, designer and PDF
  overlay: there is no second stamp system. The Accounting entry only filters the list, restricts the offered kinds, and is reachable by the
  Accounting Adviser.
* The right to configure a stamp is the group **Document Stamp Manager** (`ncollection_branding.group_stamp_manager`), implied by Settings
  administrators and by `account.group_account_manager` (the Accounting Adviser). An ordinary accountant or any other internal user can print and
  view stamped documents but cannot create, change or read a stamp.
* **Which kinds a user may stamp is decided by the model, not by the form.** Each document type names the groups allowed to stamp it
  (`ncollection.document.type.manager_group_ids`; the five accounting kinds name `account.group_account_manager`). Settings administrators may stamp every
  kind; anyone else may create, change or delete a stamp only if EVERY kind on it names a group they hold. The check (`_nc_check_kinds_allowed`) runs on
  `create`, `write` (before and after) and `unlink` against the STORED kinds, so a crafted many2many command, a `copy`, direct model or RPC calls, or a
  hostile context cannot reach another kind; the document types themselves are read-only for everyone but data. Company isolation is the separate record rule.
* **The image is group-restricted** (`groups=` on `ncollection.document.stamp.image` and `res.company.nc_stamp`). Odoo checks a field's groups on
  `/web/image`, RPC `read` and the form, so an ordinary user gets a placeholder / AccessError, not the seal. The overlay still draws it: it reads the
  bytes with a narrow `sudo()` limited to that one field on the one stamp the (company, kind, paper) selection chose, server side; the bytes never
  reach the client. A report context cannot widen that (`nc_no_stamp` is honoured only for `env.su`; the preview context is checked by
  `_nc_may_preview`). Another company's stamp is invisible by the company record rule.

## Proof

* `scripts/dev/pdf_hosts.sh` makes the container resolve each tenant's real `web.base.url` host (idempotent). A PDF with exit 0 may still
  be unstyled: **render it, convert it to an image, look at it** (the QA scripts used for this work are described in R-035).
* Tests: `ncollection_branding/tests/test_stamp_overlay.py`, `test_document_stamp.py`; `ncollection_account_core/tests/test_document_stamp.py` (real
  wkhtmltopdf); `ncollection_core` View route and parity tests; Playwright `invoices-first.spec.ts`, `product-branding.spec.ts`; `ncollection_account_core/tests/test_document_stamp.py::TestAccountingStampEntry` (menu / group / domain, adviser may configure, accountant may not, multi-company) and `ncollection_branding/tests/test_stamp_image_security.py` (the image is group-restricted: model, `/web/image`, report context, overlay still works, no cross-company leak).
