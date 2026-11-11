# Invoice layout and calculations (`ncollection_account_invoice_layout`)

Customer invoices, vendor bills, and (where Odoo uses the same lines) credit notes and vendor refunds. Every figure comes from
Odoo's own tax engine (`account.tax._add_tax_details_in_base_line`); the module owns no tax formula. Tests:
`make test m=ncollection_account_invoice_layout` (33 tests, all assertions on real figures and journal items).

## 1. Definitions (one set, used everywhere)

| Term | Meaning |
|---|---|
| gross | `quantity x price_unit`, in the basis of the Price column (tax-excluded, or tax-included when the line's taxes are price-included) |
| Disc.% | Odoo's `discount`: the percentage applied to gross |
| **Discount** (new column) | the same discount as MONEY, in the basis of Price: `gross x Disc.% / 100`, rounded by the document currency |
| **Amount** | Odoo's `price_subtotal`: discounted, tax-excluded, currency-rounded (unchanged meaning) |
| **Total incl. Tax** | Odoo's `price_total` |
| P | sum of the product lines' Amount, excluding invoice-adjustment lines |

## 2. Percentage mode and fixed-amount mode

A line is in exactly one mode (`nc_discount_mode`); the number the user typed last is the source.

* **Percentage** (typed Disc.%): `nc_discount_percent` is the source; Discount = round(gross x Disc.%/100). Quantity or price change: the
  percentage stays, the money amount follows.
* **Fixed amount** (typed Discount): `nc_discount_fixed` is the source; Disc.% = fixed / gross x 100. Quantity or price change: the money
  amount stays, the percentage follows. Both cannot stay fixed: when gross changes one of them must move, so the typed one wins.
* The tax engine is fed the EXACT percentage (fixed / gross x 100), not the 2-decimal stored one, so the booked subtotal is gross - fixed to
  the cent; the rounded Disc.% shown on screen can never move the books.
* A fixed discount larger than the line's value, or of the opposite sign, is refused (server constraint, not only the form).

Worked example (tests `test_percentage_*`, `test_a_fixed_discount_*`): quantity 3, price 100, 10 %: Discount 30.00, Amount 270.00.
Type Discount 45 and change quantity to 5: Discount stays 45.00, Disc.% becomes 9.00, Amount 455.00. Type Disc.% 5 afterwards: mode returns
to percentage, Discount 25.00. A 10.00 discount on 300.00 is 3.3333 %, stored 3.33, booked 290.00 exactly.

## 3. Editable Amount and Total incl. Tax

Typing a target solves the unit price; quantity, taxes and the discount (percentage or fixed) are never touched.

1. The engine's result is affine in the price (percentage, price-included and fixed taxes all are). Two probes (price 1 and 2) give the exact
   price `p = 1 + (target - f(1)) / (f(2) - f(1))`.
2. `p` is snapped to the price precision (`Product Price`, 2 decimals by default) and the neighbours (+-3 steps) are evaluated with the engine's
   own ROUNDING; the one whose booked figure is closest to the target wins (ties: the current price, then the exact price).
3. If the closest figure is not the target (for example 100.00 over quantity 3 at 2-decimal prices gives 99.99 or 100.02), the user is told in the
   form ("Closest figure used") and the closest figure is booked. Raising the Product Price precision gives exact results.
4. Refused with a message: quantity 0; a 100 % discount or a fixed tax only (the price has no effect on the total).

Examples (15 % VAT): quantity 2, price 50: Total incl. Tax typed 230.00 -> price 100.00, Amount 200.00, tax 30.00. Quantity 2, 10 %: Total typed 207.00
-> price 100.00, Disc.% stays 10, Amount 180.00. Fixed discount 20, quantity 2: Amount typed 280.00 -> price 150.00, Discount stays 20.00.
Preserving a tax setting does not preserve the tax AMOUNT: the amount is recomputed from the new taxable base.

## 4. Charges and discounts of the invoice (footer block)

Administrators define `ncollection.invoice.adjustment.type` records (Accounting > Configuration > Invoice Layout & Calculations > Charges &
Discounts): label (translatable), charge or discount, fixed or percentage, basis, account, taxes, analytic distribution, applicability (customer /
vendor / both), company, sequence, "on every new invoice". Data, never code: there is no expression language and nothing is evaluated.

Each invoice carries rows of that definition (the block above the Terms and Conditions). A non-zero row is booked as a REAL invoice line (quantity 1,
price +amount for a charge, -amount for a discount, the type's account, taxes and analytic distribution), so taxes, the journal entry, receivable /
payable and reconciliation are Odoo's own. `amount_total` is never edited.

Calculation order: (1) line discounts -> P. (2) invoice discounts: fixed, or % of P; never more than P. G = their total. (3) charges: fixed, or % of
P ("lines") or of P - G ("lines after the invoice discounts"). (4) taxes: the taxes of each adjustment line. Adjustment lines are excluded from every
basis, so an adjustment cannot feed itself.
Untaxed Amount = P - G + F. **Total Discount** = line discounts (tax-excluded) + G. **Total After Discount** = Untaxed Amount - F = P - G. Both are shown above
Untaxed Amount only when something was discounted.

Worked example (15 % VAT on every line; test `test_combined_line_discount_global_discount_and_freight`): lines 2 x 100 at 10 % (180) and 1 x 50: P = 230.
Invoice discount 30, freight 25: Untaxed Amount 225, Total Discount 50 (20 + 30), Total After Discount 200, tax 33.75, Total 258.75.

Draft invoices only: a posted invoice keeps the lines it was posted with, and these fields refuse to change them. Duplicates and credit notes copy the booked
lines as ordinary lines (identical figures). A booked line is changed only from the block, never by hand.

## 4a. Presentation: adjustments are never product lines (19.0.1.2.0)

An adjustment is booked as a real `account.move.line` of the invoice (its account, its taxes): taxes, totals, the
receivable / payable, the invoice amount audit and the e-invoice read it like any other line. It carries the explicit
link `account.move.line.nc_adjustment_id`, and that link decides where it is SHOWN:

| Surface | Shows the booked adjustment lines? | How |
|---|---|---|
| Invoice form, Invoice Lines table | no | `static/src/commercial_lines.js`: the list renderer of the invoice lines (the one that loads `nc_adjustment_id`) sees a view of its list without the linked lines; rows, footer sums, keyboard navigation and the empty state use it, every edit / add / delete / save runs on Odoo's own list. Data, not DOM: it survives a reload, an onchange and a save. |
| Invoice form, Charges & Adjustments block | yes, one row per adjustment | the adjustment records |
| Invoice form, Journal Items tab | yes, every booked line | Odoo's own `line_ids` |
| Printed invoice / bill / credit note, product table | no | `account.move._get_move_lines_to_report` |
| Printed invoice, Charges & Discounts section | yes, one row per adjustment (label, value, taxes, signed amount) | `report/invoice_adjustments.xml`, from the booked lines |
| Printed totals | included once | Odoo's own `tax_totals` |

Why not a separate `display_type` (as Odoo does for cash rounding / early-payment discount lines): every consumer of
`invoice_line_ids` (tax computation while editing, the UBL / PINT-AE builders, reports, stock and sale links) would
then miss the adjustment and need its own change. Keeping the line a product line and moving only the PRESENTATION
keeps one accounting truth.

**Taxes from** (on the adjustment type): *The taxes below* books one line with the type's taxes (charges by default; a
VAT-able freight carries the sales VAT, a charge outside the VAT scope carries none). *Same taxes as the invoice lines*
(discounts by default) splits the amount over the VAT groups of the product lines, pro rata to each group's tax-excluded
subtotal, one booked line per group, the rounding remainder on the largest group: each VAT base is reduced by its own
share. Worked example (browser-verified): 300 at 5 % + 100 at 0 %, freight 40 at 5 %, 5 % global discount, 5.00 fixed
discount -> discount lines -15 (5 %) / -5 (0 %) and -3.75 (5 %) / -1.25 (0 %); VAT 5 % on 321.25 = 16.06, VAT 0 % on
93.75; untaxed 415.00, total 431.06. A price-included tax is grossed up so the booked tax-excluded amount is exact.

**Credit notes and reversals** carry the invoice's adjustments AS adjustments, linked to the lines Odoo copied, so they
show and print the same way.

## 5. Posted invoices (unchanged policy)

No new editing restriction and no new way to re-price a posted line: the editable fields refuse a posted invoice. Core Odoo still lets `price_unit` of a
posted line be written (INVOICE_AMOUNT_AUDIT.md section 7); that stays a separate accounting-policy decision. The invoice amount audit records
every amount change of these fields like any other (tests in `test_invoice_adjustments.py`).

## 5a. Live recalculation while editing (`_onchange_nc_adjustments`)

While an invoice is open for editing, changing a product line or a row of the Charges & Adjustments block re-runs the ONE calculation
(`account.move._nc_adjustment_amounts`, the same function the save uses): every row shows its money at once, the "Untaxed after Charges & Discounts" preview
(`nc_adjusted_untaxed`) follows, and an adjustment whose invoice line already exists re-prices that line, so Untaxed Amount, Taxes and Total follow before saving.
A row added just now books its invoice line when the invoice is saved (it is keyed on the adjustment, so it can never be booked twice). When the web client sends the
live price of a booked line back on save, the server does not apply that echo; the save re-books the authoritative value from the adjustment. A hand edit of a booked line
is still refused. Browser-measured: a 10 % global discount moved from 23.00 to 95.00 AED, unsaved, when a line quantity went from 2 to 10.

## 5b. Column order and visibility (`column_arrange.js`, `ncollection.invoice.column.pref`)

* A maintainable `patch(ListRenderer.prototype)` limited to the invoice-lines list (`account.move.line` with the Discount column): only `getActiveColumns` (order),
  `computeOptionalActiveFields` / `saveOptionalActiveFields` (visibility) and one new entry in the existing column menu change. Cells, inline editing, onchange and keyboard
  navigation are Odoo's own. The invoice lines use the account module's primary template copy, so the menu entry extends BOTH `web.ListRenderer` and
  `account.SectionAndNoteListRenderer`.
* **Arrange columns** (column menu, last icon of the header row): a dialog with drag and drop (desktop) and up / down buttons (touch, keyboard), a check box per optional
  column, and the buttons **Save for me**, **Set as company default** (Accounting Managers only) and **Back to company default**.
* Resolution: the user's own row, else the company default, else the view's order. Customer invoices, vendor bills and credit notes share one list key, so they behave identically.
  The Odoo column menu check boxes are also saved as the user's own preference on the server, not only in the browser.
* Storage: one row per (company, user, list) holding column NAMES only (validated against `account.move.line`). A user reads their own row and the company default and
  writes only their own; the company default is writable by Accounting Managers only (record rules AND a server check). Changes to a company default are tracked in the chatter
  of the record (Accounting > Configuration > Invoice Layout & Calculations > Invoice Column Defaults), as are changes to the charge / discount definitions (`mail.thread` tracking).
* Phones: Odoo shows the invoice lines as cards there (no table, so no column menu); the arrangement applies to the table layouts (desktop, tablet). The charges block and the discount
  summary were checked at 375 px, English and Arabic: no horizontal overflow.

## 5c. Configuration map

| Setting | Where | Who |
|---|---|---|
| Charge / discount definitions (kind, fixed / %, basis, account, taxes, analytic, applicability, auto-add, sequence) | Accounting > Configuration > Invoice Layout & Calculations > Charges & Discounts | Accounting Manager (ORM + company rule; changes tracked) |
| Company default column order and hidden columns | the Arrange columns dialog, or Invoice Column Defaults | Accounting Manager (tracked) |
| User column order and visibility | the Arrange columns dialog / the column menu | the user, own row only |
| Taxes and accounts of a charge | on the definition (editable per invoice row for the tax) | Accounting Manager (definition), invoicing users (row) |
| Calculation order and bases | fixed by design (section 4); no expression language | - |

## 6. Limits (stated, not hidden)

* A global discount over lines with different tax rates is split pro rata over the VAT groups when its type takes its
  taxes from the invoice lines (the default for discounts); with "The taxes below" it books one line with those taxes.
* A booked discount line has a negative unit price. PINT-AE prefers a document-level allowance (BG-20) for an invoice
  discount; the e-invoicing engine validates the XML and blocks what the official rules refuse, it never adjusts it.
* A freshly added adjustment row books its line on save (its amount and the preview are live; Untaxed / Total change on save).
* Drag and drop is a desktop gesture; touch and keyboard use the arrow buttons.
* Phones show invoice lines as cards: the column arrangement does not apply there.
