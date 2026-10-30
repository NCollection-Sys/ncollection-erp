# UAE e-invoicing (PINT-AE)

Module `ncollection_uae_einvoicing` (engine), master data in `ncollection_account_localization_uae`, validator worker in
`satellites/einvoice_validator`. Specification phase documents (Phase 0 audit, Phase 1A specification) are the reasoning; this file
is what the code now does and why. Status of every compliance claim is stated: **FACT** (verified in a cited source or by a test),
**DECISION** (taken by the project owner), **OPEN COMPLIANCE QUESTION (OCQ)** (not conclusively established, not guessed).

## 0. Regulatory baseline (re-baselined 2026-10-28)

The Ministry of Finance eInvoicing page (https://mof.gov.ae/en/about-us/initiatives/einvoicing/) is the primary source. Every entry
below was read from the published PDF; the registry in code is `lib/sources.py` (MoF instruments) and `lib/spec.py` (Peppol
specifications). **Superseded and no longer cited as current:** Guidelines V1.1 (1 Jun 2026) and Mandatory Fields V1.0 (23 Feb 2026).

| Source | Version / date | What it fixes for this engine |
|---|---|---|
| UAE Electronic Invoicing Guidelines | **V2.0**, Oct 2026 | the five-corner model (§5.1), scope (§6), exclusions (§7), phases (§8), document categories / scenarios / tax categories (§10), storage (§5.4, App 4), advances and retention (App 5), identification and classification (App 6) |
| UAE Electronic Invoice Mandatory Fields | **V1.1**, 28 Oct 2026 | 51 fields for an electronic tax invoice (§4.1), 49 for a commercial electronic invoice (§4.2) |
| Ministerial Decision 243/2025, as amended by **MD 197/2026** | MD 197 in force 1 Oct 2026 | exclusions (Art 4), credit-note cases (Art 6(2)), issuance deadline (Art 6(4)–(5)), agent (Art 8), self-billing (Art 9), retention (Art 11); Art 12 repealed |
| Ministerial Decision 244/2025, as amended by **MR 66/2026** | MR 66 published May 2026 | phased implementation dates |
| Ministerial Decision 168/2026 | in force 1 Oct 2026 | ASP accreditation (replaces MD 64/2025 and MR 56/2026); no engine behaviour depends on it |
| Cabinet Decision 106/2025 | final version, 8 Dec 2025 | violations and administrative penalties |
| PINT AE Billing | **1.0.4**, 2026-06-02, Final (PDK 1.4.4) | the vendored, unmodified rule set (§2) |
| PINT AE Self-Billing | **1.0.4** | same data model; its own type codes 389 / 480 and 261 / 81 (`spec.COMPANION_SPECS`) |
| UAE Tax Data Document | **1.0.4**, 2026-07-29, Final | reported by the ASPs (Corners 2 and 3), not by this engine |

**Current effective requirements** (each is the source's own wording, summarised):

* **Six document categories** (Guidelines V2.0 §10.1): electronic Tax Invoice, electronic Tax Credit Note, Commercial Invoice,
  Electronic Credit Note, self-billed electronic Tax Invoice, self-billed electronic Tax Credit Note. There is no self-billing of
  commercial invoices (§10.3) and no "provisional invoice" category (§10.1).
* **Eight combinable scenarios** (Guidelines V2.0 §10.4; Mandatory Fields V1.1 field 5, the BTAE-02 flags in this order): free zone,
  deemed supply, margin scheme, summary invoice, continuous supply, disclosed agent billing, e-commerce, export. Several may apply
  to one document (§10.4, p. 31).
* **Tax categories** (Guidelines V2.0 §10.5): standard rate, exempt, outside the scope, reverse charge, zero rated, margin scheme.
  **Domestic reverse charge** (§10.5.1) applies only between two VAT registrants, for electronic devices, precious metals and stones,
  crude or refined oil, natural gas, pure hydrocarbons and **metal scrap**; the invoice carries no VAT, a **narrative** of the
  reverse-charge reason, and the goods type. Imports of concerned goods/services carry no e-invoicing requirement.
* **Scope** (Guidelines V2.0 §6.1–6.2): every Business Transaction of a Person in business (B2B, B2G, G2B, G2G); supplies to or from
  natural persons who are not in business are outside the scope.
* **Exclusions** (MD 243 Art 4 as amended by MD 197 Art 2): sovereign Government transactions not in competition with the private
  sector; airline passenger transport with an electronic ticket and its ancillary services with an EMD; airline cargo with an air
  waybill for 24 months from the date the e-invoicing system becomes effective; financial services exempt or zero-rated under
  Art 42 of the VAT Executive Regulation; **transactions between members of the same Tax Group for 24 months from 1 January 2027
  (through 31 December 2028)**. Guidelines V2.0 §6.3.2.1 describes the Tax Group window as a grace period that changes the timing
  only; the Decision text lists it as an Excluded Transaction. The engine follows the Decision text and cites both.
* **Issuance deadline** (MD 243 Art 6(5) as replaced by MD 197 Art 2): **14 Business Days** from the Date of Business Transaction,
  subject to Art 6(4) (a Registrant issues within the timeline of the VAT Law, a separate rule).
* **Storage** (MD 243 Art 11 as replaced by MD 197 Art 2; Guidelines V2.0 §5.4, App 4): retention for the Tax Procedures Law period,
  producible to the Authority; **the former requirement to store inside the UAE is removed**.
* **System failure** (MD 197 Art 1(3) and Art 3): the definition and **the notification obligation of former Art 12 are repealed**. The
  engine implements no system-failure notice. (Guidelines V2.0 App 3 still asks Persons to make sure their ASP notifies the FTA of
  service disruptions; that is the ASP's act.)
* **Timetable** (MD 244 Art 5 as amended by MR 66; Guidelines V2.0 §8.3): Revenue ≥ AED 50m: appoint an ASP by **30 Oct 2026**,
  implement by **1 Jan 2027**; Revenue < AED 50m: 31 Mar 2027 / 1 Jul 2027; Government Entities: 31 Mar 2027 / 1 Oct 2027. Pilot and
  voluntary adoption from 1 Jul 2026 (§8.1–8.2).
* **Penalties:** Cabinet Decision 106/2025. Guidelines V2.0 §11.2 cites a Cabinet Decision No. 173 of 2026 that is **not published** on
  the MoF page: it is not cited as a fact (OCQ-19).

## 1. What it is, and is not

* NCollection is **Corner 1** of the five-corner model: it prepares the document and hands it to the customer's Accredited Service
  Provider (ASP). It is not the FTA and not an ASP. [FACT: UAE Electronic Invoicing Guidelines V2.0 §5.1]
* **No ASP is selected or built in.** The core holds a provider-independent **transport engine** (§9a: queue, idempotency,
  retries, callbacks, confirmations) that reaches an ASP only through the adapter interface; a `ncollection_uae_einvoicing_<asp>`
  module is the only place a provider's network calls, formats and credentials' *use* live. There is still no HTTP client in the
  core. Decided 2026-10-28: the engine is built and proven against the contract adapter now; a real adapter follows the choice of
  an ASP and its sandbox, and **release stays blocked until one is proven**.
* **Confirmations are the ASP's.** The Tax Data Document and the report to the FTA are the ASP's acts; NCollection consumes and
  stores the ASP's confirmations and never generates a TDD itself (decided 2026-10-28; Guidelines V2.0 §5.1).
* **Scope:** customer invoices (`out_invoice`, 380 / 480) and customer credit notes (`out_refund`, 381 / 81) on PINT AE
  **Billing**; and, since Cycle 3 (2026-10-29), **self-billed** documents on PINT AE **Self-Billing** (§9d): a vendor bill
  (`in_invoice`, 389) or vendor refund (`in_refund`, 261) that THIS company issues for its supplier under an active
  self-billing agreement. A vendor bill is e-invoiced ONLY that way: without an agreement, creating its e-invoice is refused,
  and a bill that itself came from a received e-invoice can never be self-billed. Inbound documents (§9c) are stored, matched
  and turned only into a DRAFT vendor bill, or for a received self-billed document a DRAFT customer invoice.
* The **PDF is unchanged**: it is the printable representation. The e-invoice is the structured XML. No QR code or barcode is added
  (the UAE e-invoice has none). [FACT: Guidelines V2.0 §5.3]

## 2. Pinned official resources

`resources/pint_ae/1.0.4/` holds **unmodified** copies of: the two Schematron bundles (`.sch` and the preprocessed `.xslt`), 17 code
lists, 27 invoice and 3 credit-note samples. `MANIFEST.txt` gives the sha256 of every file and of the source archive
(`1e8b0bd5…491f`, 1,019,347 bytes, `Last-Modified` 2026-07-29, release 1.0.4 of 2026-06-02, status Final). `lib/spec.py` is the only
place the release, the specification identifiers and the constants live; `verify_resources()` detects a changed, missing or
unlisted file and a test fails if the version string appears anywhere else in `lib/`. Adding a release is a new profile and a new
folder, not a code search (the Schematron was revised six times in its first year).

`resources/pint_ae_sb/1.0.4/` holds the **PINT AE Self-Billing 1.0.4** package the same way (profile `pint-ae-sb-1.0.4`; source
`https://docs.peppol.eu/poac/ae/upcoming/pint-ae-sb/resources.zip`, sha256 `7e6e58f1…3d15`, 784,521 bytes, `Last-Modified`
2026-07-29, PDK 1.4.4): the two Schematron bundles, the same 17 code lists, and the two official samples (`Self Billing.xml`,
`Self billing tax credit note.xml`). Compared byte for byte with Billing 1.0.4 (2026-10-29): the jurisdiction rules are
**identical**; the UBL rules differ in ONE assert, `ibr-cl-01` (type codes 389/480 and 261/81 instead of 380/480 and 381/81);
the code lists are identical except `UNCL1001-inv` / `UNCL1001-cn`. `CustomizationID` `urn:peppol:pint:selfbilling-1@ae-1`,
`ProfileID` `urn:peppol:bis:selfbilling`. Its own `MANIFEST.txt`, so its rule-set fingerprint differs from Billing's.

## 3. Upstream artifact discrepancies (recorded, never "fixed")

Registered in `lib/upstream.py`, each with evidence, the engine's handling and a check that it is *still true* of the pinned files, so a
new release that corrects one forces a human review.

| Id | Finding | Handling |
|---|---|---|
| `greek-nu-margin-code` | the code-list file gives the margin-scheme category as GREEK capital Nu (U+039D); the Schematron and the official sample use LATIN `N` | the engine emits Latin `N`; the file is read as published; `tax_categories` is an explicit tested set |
| `hs-sac-min-digits-placeholder` | rule `ibr-187-ae` (minimum digits of HS/SAC) is a placeholder: `test="true()"`, message says "X digits" | digits-only is an NCollection *warning*; no length is enforced (OCQ-5) |
| `self-billing-type-codes-missing` | the BILLING type lists contain 380/480 and 381/81 only; the self-billing codes are in the separate PINT AE Self-Billing 1.0.4 lists (389/480, 261/81) | self-billing is its own profile and rule set (§2, §9d); Billing never emits 389/261 and its rules refuse them (`ibr-cl-01`, proven both ways in `e2e_check.py`) |
| `self-billing-credit-rules-not-applied` | `ibr-055-ae` (preceding reference), `ibr-158-ae` (credit reason), `ibr-124-ae` (no VAT point date) and `ibr-151-ae` (not only E/O) name only 380/381/81, so they never fire for 389/261, in the Self-Billing package too | the engine applies the same four requirements to self-billed documents as ITS OWN rules (`NCOLLECTION:self-billing-ibr-055-ae` …): a self-billed tax credit note is a Tax Credit Note (VAT Decree-Law Art 70; Guidelines V2.0 §10.3) |
| `ecommerce-sample-flagged-as-export` | the official sample "Supply through e-commerce" carries transaction type `00000001`, the EXPORT position (`ibr-152-ae`), not e-commerce `00000010` (`ibr-142-ae`) | the engine follows the rules' positions (`identity.FLAG_ORDER`), not the sample |
| `exempt-only-tax-invoice-refused` | fatal `ibr-151-ae` refuses a 380/381 whose lines are all E/O, while Guidelines V2.0 App 6 §18.3.1 says 480 is not for exempt or zero-rated supplies | DECISION 2026-10-28: follow the fatal rule (all E/O ⇒ 480/81; any S/Z/AE/N ⇒ 380/381); OCQ-20 |
| `billing-frequency-description-code` | `ibr-160-ae` tests `InvoicePeriod/cbc:DescriptionCode`, the official sample uses `cbc:Description` | the engine writes `cbc:Description` like the sample; the 'Others' note is not enforced by anyone yet (follow-up) |
| `metal-scrap-goods-type-missing` | Guidelines V2.0 §10.5.1 lists metal scrap as reverse-charge goods; fatal `ibr-006-ae` accepts only the five shipped GoodsType codes | metal scrap is recognised in the tax map and BLOCKED with `NCOLLECTION:rcm-goods-type-not-in-release`; no code invented (OCQ-6) |
| `no-endpoint-format-rule` | no official rule checks the endpoint's format (a 9-digit seller TIN validates) | `NCOLLECTION:tin-must-be-10-digits` is our own rule and is reported as such |
| `emirate-rule-shadowed` | `ibr-128-ae` sits in the generic address rule, after the seller/buyer-specific ones in the same pattern; only the first matching rule fires, so an invalid emirate (`DU`) **validates clean** | the engine's explicit emirate map is the only enforcement |
| `exemption-code-outside-list` | an official sample uses `VATEX-AE-EDU`, outside the shipped four-code list, and validates | presence is official (`ibr-167-ae`); membership is an NCollection warning |
| `payment-means-list-differs-from-website` | shipped list has nine codes; the website says 30/48/54/55 | the pinned artifact wins; `48` is never emitted (OCQ-17) |

Every finding the engine reports names whose rule it is: `OFFICIAL:<assert id>` or `NCOLLECTION:<name>`. An NCollection rule is
never presented as official.

## 4. Identity: nine identifiers, nine fields

Odoo invoice number (`move_id.name`) · NCollection record id (`name`, own sequence) · PINT invoice number (a *snapshot*,
`invoice_number`) · UUID · seller TIN · endpoint (`*_endpoint_id/_scheme`) · participant id (`asp_participant_id`, written by an
adapter result only) · ASP / exchange references · FTA reference. No field stands in for another (a test proves it). TRN, TIN,
participant id, endpoint id and scheme id are five things:

* `res.partner.nc_trn` — the UAE VAT TRN, optional, 15 digits when set.
* `res.partner.nc_tin` — the UAE e-invoicing participant TIN, optional, 10 digits when set.
* **Neither is ever derived from the other or from `vat`; there is no copy helper and no fallback to `vat`**; they are not
  synchronised between a company and its contacts (`copy=False`, outside `_commercial_fields`).
* **OCQ-1 (open, re-confirmed against the current versions):** the two current official documents still disagree on the TIN's
  origin — Mandatory Fields V1.1 (28 Oct 2026) §2 and glossary: the first 10 digits of the *Corporate Tax* TRN; Guidelines V2.0 §2
  and App 6 §18.1: the first 10 digits of the TRN *the Person has been issued*, its own (both: not the Tax Group representative's;
  App 6 §18.1.2 sends a Person whose VAT and Corporate Tax profiles show different TINs to the FTA). Therefore the TIN is stored
  explicitly and never computed, and the engine does not choose between the two readings. Guidelines V2.0 App 6 §18.1.1 also fixes
  where the seller's identifier goes: a VAT-registered seller's TRN in IBT-031, a seller not registered for VAT puts its TIN in
  IBT-032 (rule ibr-177-ae); the builder does exactly that.
* **UUID (OCQ-14, resolved Cycle 4 as far as the sources allow):** Guidelines V2.0 §5.2 #7 assigns *generating* the UUID to the ASP,
  but fatal `ibr-193-ae` requires `cbc:UUID` in the document Corner 1 hands over, and official validation (and the ASP's own) runs on
  that document. So the engine is the authority for the DOCUMENT's `cbc:UUID`: generated once (RFC 4122 v4) at first preparation,
  locked at submission, never reused; the ASP's identifier is a DIFFERENT field (`asp_reference`; A-Cube's `invoiceId`). A-Cube's
  published contract returns its own resource uuid and does not say it rewrites `cbc:UUID` — SANDBOX-VERIFY; if an ASP does, its value
  is stored as the provider reference, never written over ours.
* **Foreign buyer (R05, Cycle 4; Guidelines V2.0 §18.2.3):** a buyer whose country is not AE, with no UAE TIN, and with
  `nc_foreign_tax_id` + `nc_foreign_tax_scheme` (localization master data; the scheme is 4 digits, never 0235) gets endpoint
  `0235:9900000098` (source `predefined_foreign_buyer`) and IBT-046 = its foreign identifier with the ICD scheme; IBT-048 is not
  populated. Readiness checks the scheme against the ICD list read from the pinned `ibr-cl-10` itself (`codelists.icd_codes`), and
  `ibr-135-ae` accepts the identifier. A UAE buyer is never treated as foreign. Tests: `tests/test_foreign_buyer.py` (5) and the
  real-move case `real-foreign-buyer-380` (official rules).

`vat` itself is untouched: it is Odoo's country-agnostic Tax ID (validated per country, used for fiscal positions, printed as the TRN by
the GCC invoice, and holding foreign VAT numbers for foreign customers), so it does not carry the exact semantics "the UAE VAT TRN".

## 5. Rounding: Odoo is the truth, the engine reconciles and blocks

Flow: *Odoo accounting values → independent PINT-AE calculation → comparison → eligible / blocked.* The XML carries Odoo's booked
figures (taxable per category, the tax lines Odoo posted, totals); the recomputation is only a check and never feeds the XML. Nothing
adjusts a value to make a document pass; a document that cannot satisfy the official rules is **blocked** with the rule, the values
and "nothing was changed", and the accounting document is untouched (tests assert this).

Tolerances are the official ones, read from the pinned Schematron: **exact** for line net (`ibr-147-ae`), totals (`ibr-co-13…16`), the
zero tax of Z/E/O/AE/N breakdowns (`aligned-ibrp-z/e/o-09`, `ae-09-ae`, `ibr-108-ae`) and the standard rate (`ibr-190-ae`);
**±AED 0.02** only for a Standard-rated breakdown's tax (`aligned-ibrp-s-09`, `u:slack(…, 0.02)`). Outcomes: `exact`,
`within_tolerance` (eligible, shown), `blocked`. *(The Phase 0 audit read S-09 as exact; the official Schematron corrected that.)*

Two honest limits of this check. (1) `ibr-co-16` (amount due = total − prepaid + rounding) is an **identity** for every document the
mapper produces, because the mapper maps no prepayment and no rounding: a document with cash rounding is therefore **blocked**
(`NCOLLECTION:cash-rounding-unsupported`) rather than passed through a vacuous check. The check stays in the engine so a payload that
does carry them is still tested. (2) For a foreign-currency document the stated `CalculationRate` is the rate-table rate for the invoice
date read at preparation time, not necessarily the rate used when Odoo posted (a later edit of the rate table changes the stated rate but
not the booked AED amounts); the AED tax and total are Odoo's booked values. Source of the rate: **OCQ-8**.

### Analysis preserved (Phase 1A)

**Real data** (read-only, five UAE databases, 23 posted customer documents, all AED): 0 mismatching groups; every (document, rate)
group has one line (three in one demo group); no discount, multi-tax line, negative line, price over 2 decimals, foreign currency or
posted credit note exists. This data cannot quantify the problem.

**Synthetic** (Decimal half-up, Odoo `round_per_line` against the PINT recomputation, one 5% category, 200,000 documents per scenario,
seed 20261022). *This was a one-off analysis: its script and raw outputs are not in the repository, so the table below is the preserved
RESULT, not something the repository can regenerate. Re-measure per pilot tenant (OCQ-9) before relying on it.*

| Scenario | exact | Δ0.01 | Δ0.02 (tolerated) | **blocked (>0.02)** |
|---|---|---|---|---|
| A retail 1–10 lines, price 1–200.00 | 54.93% | 39.19% | 5.55% | **0.33%** |
| B many small lines 5–40, price 0.50–50 | 25.32% | 38.61% | 20.83% | **15.24%** |
| C B2B 1–5 lines, whole-dirham prices | 100% | 0 | 0 | **0%** |
| D discounts 5/10/12.5/15% | 57.07% | 38.37% | 4.37% | **0.20%** |
| E fractional quantities | 58% | 38% | 3.69% | **0.13%** |
| F single line | 100% | 0 | 0 | **0%** |

Scenario B by line count: 5 lines 0% blocked → 8 lines 0.22% → 9 lines 0.49% → 10–19 lines 4.88% → 20–39 lines 23.16% → 40+ lines 35.02%.
Affected: multi-line documents with several unrounded-cent lines at one rate; single-line and whole-dirham B2B are immune. Not
simulated: multiple rates/categories, document allowances, foreign currency (measure per pilot tenant before go-live, OCQ-9).
**Recommended accounting-compatible solution:** keep `round_per_line` (no policy change); apply the official tolerance; block the rest
with the reason; run the same reconciliation over each tenant's posted history *before* go-live to predict its blocked rate; only if
that rate is material, take a per-tenant accounting decision about `round_globally` (it changes tax for every future posting).

## 6. Master data, classification, payment means, discounts

* **HS/SAC are conditional** on the item type the product declares (`G` → HS `ibr-184`, `S` → SAC `ibr-185`, `B` → both `ibr-186`); a
  product with no declared type is not forced to carry either (a warning, no block). They are enforced at e-invoice time, never on the
  product form.
* **Emirates:** explicit map Odoo `AZ DU SH UQ FU AJ RK` → `AUH DXB SHJ UAQ FUJ AJM RAK`; Odoo's codes are not PINT's; an unmapped
  emirate is an error. [the official rule is shadowed, §3]
* **Payment means:** the code list is the pinned nine; the mapping is explicit (payment-method/journal map, company default, per-document
  override); nothing is inferred from `manual`; ambiguity and absence are errors; credit notes and deemed supply omit it (`ibr-191-ae`);
  `30` needs the payee account (`ibr-192-ae`). In the audited tenants every method is `manual` and no journal or company has a bank
  account, so no mapping could be derived from the data.
* **Allowances and charges** (D-13): a per-document-line toggle on the e-invoice line (not on the product, not on the accounting line).
  A flagged line becomes a document-level allowance/charge with its VAT category (`aligned-ibrp-032/037`); category `N` is refused
  (`ibr-114/115-ae`); an unflagged negative line is refused. Line discounts become the price discount (BT-147/148), as in the official
  samples; `OCQ-4` (precision of a derived net price) stays open.
* **Units:** an explicit closed map to UN/ECE Rec 20; an unknown unit blocks (Odoo core silently uses `C62`).
* **GTIN:** a product barcode is sent as a GTIN (scheme 0160) only if it is a GTIN-8/12/13/14 with a correct GS1 check digit; any other
  barcode is not sent.
* **Currency and company:** UAE e-invoices are reported in AED, so a company whose books are not in AED is blocked
  (`NCOLLECTION:company-currency-not-aed`). The e-invoice's company is always the accounting document's company (never the session's
  active company). A company must be switched on in its configuration (`enabled`) before anything can be prepared.
* **Language:** the payload is always built in one language (`en_US`), so the same document produces the same payload and XML whoever,
  in whichever UI language, prepares or generates it.
* **Volume-discount credit notes:** `ibr-055-ae` allows no invoice reference with reason `VD`, but Odoo's *reverse* action always links
  the original invoice. Such a note is therefore **blocked with the rule named** until the user chooses another reason or issues it
  without the reversal link; the engine does not silently drop the link.

## 6a. Scenario matrix (BTAE-02; Guidelines V2.0 §10.4)

The eight positions of the transaction-type code, in the order the official rules test them (`identity.FLAG_ORDER`). "Official"
= the rule is the pinned Schematron's (fatal unless noted); every row has a POSITIVE real-move case (`tests/test_scenario_matrix.py`,
exported and validated by the official rules in `make einvoice-e2e`) and a NEGATIVE case (readiness reports the rule before XML).

| # | Scenario | Code | Mandatory (rule) | Forbidden | Document types | XML | Tests (+/−) |
|---|---|---|---|---|---|---|---|
| 1 | Free zone | `1XXXXXXX` | beneficiary ID BTAE-01 (`ibr-007-ae`) | — | 380/381/480/81 | `cac:BuyerCustomerParty/…/cbc:ID` | `test_free_zone` / `…_without_beneficiary` |
| 2 | Deemed supply | `X1XXXXXX` | predefined endpoint `0235:9900000097`; no payment means / due date (`ibr-191-ae`, `ibr-127-ae` exempt) | on 480/81 (`ibr-157-ae`) | 380/381 | buyer `EndpointID` | `test_deemed_supply` / `…_not_allowed_on_a_commercial_invoice` |
| 3 | Margin scheme | `XX1XXXXX` | every category `N` (`ibr-116-ae`); VAT amount 0 with the rate shown (`ibr-108-ae`) | on 480/81 (`ibr-157-ae`); any non-N line | 380/381 | `TaxCategory/ID = N`, `Percent` | `test_margin_scheme` / `…_with_a_standard_rated_line`, `…_on_a_commercial_invoice`, tax-map guard |
| 4 | Summary invoice | `XXX1XXXX` | invoicing period IBG-14 (`ibr-138-ae`) | on 480/81 (`ibr-157-ae`) | 380/381 | `cac:InvoicePeriod` | `test_summary_invoice` / `…_without_period`, `…_on_a_commercial_invoice` |
| 5 | Continuous supply | `XXXX1XXX` | none fatal; billing frequency from the official list if given (`ibr-005-ae`) | — | all | `InvoicePeriod/Description` | `test_continuous_supply` |
| 6 | Disclosed agent | `XXXXX1XX` | principal ID BTAE-14 (`ibr-137-ae`); the agent's TRN (`ibr-177-ae`) | principal = the seller's TRN (`ibr-176-ae`) | all | `cac:SellerSupplierParty/…/cbc:ID` | `test_disclosed_agent_billing` / `…_without_principal`, `test_the_principal_cannot_be_the_agent` |
| 7 | E-commerce | `XXXXXX1X` | deliver-to street, city, region (`ibr-142-ae`) | — | all | `cac:Delivery` | `test_ecommerce` / `…_without_a_delivery_address` |
| 8 | Export | `XXXXXXX1` | deliver-to address unless delivered in the UAE (`ibr-152-ae`); buyer identifier not required (`ibr-135-ae`) | self-billed export (`NCOLLECTION:self-billing-export-not-applicable`) | 380/381 typically zero-rated | `cac:Delivery` from `partner_shipping_id` | `test_real_export_scenario_380` (crosscheck) / `test_export_without_a_delivery_address_abroad` |

**Combinations.** No official rule forbids two positions together except via the document type (`ibr-157-ae`); proven
together: free zone + e-commerce (`10000010`), summary + continuous supply (`00011000`), and several missing-data flags at once
report every rule. **Tax categories:** S, Z, E, O, AE (reverse charge, `test_reverse_charge`, crosscheck), N (margin) — all
reach official validation on real moves; all-E/O ⇒ 480/81 (below); out-of-scope-only ⇒ 480.

**Defects this matrix found and fixed (Cycle 3).** (1) An e-commerce supply was built WITHOUT `cac:Delivery` — the engine emitted
the deliver-to address only for exports — so every real e-commerce document would have failed fatal `ibr-142-ae`; the address now
comes from the document's delivery address for e-commerce too, and readiness reports `ibr-142-ae` first. (2) A real margin-scheme
line (Odoo tax 0%, mapped `N`) carried rate 0 and was blocked; `N` now takes the rate SHOWN from the tax map like reverse charge
(the tax map refuses an `N` tax that is not 0% or has no shown rate). (3) `ibr-176-ae` / `ibr-177-ae` (agent billing) had no
readiness check; they now do.

**The known conflict, unchanged.** Guidelines V2.0 App 6 §18.3.1 vs fatal `ibr-151-ae`: the DECISION of 2026-10-28 stands — follow
the fatal rule, so an all-Exempt / Not-subject document is 480 / 81 (`real-exempt-only-480`, e2e negative control "exempt-only
document typed 380 fails ibr-151-ae"). Not re-litigated: no newer authoritative text was found. Self-billing has NO 480 / 81 form
(§9d), so there the same document is refused instead.

## 7. Validator

The official Schematron is **XSLT 2.0** (`queryBinding="xslt2"`; `xsl:function`, `castable as`, `every … satisfies`). The Odoo image has
`lxml` (XSLT 1.0) only, and **nothing is added to it** (D-10a). The rules run, unmodified, in `satellites/einvoice_validator`
(SaxonC-HE 13.1, its own container, no database, no network, no storage). The engine's client is separate and replaceable and **fails
closed**: an unreachable, slow, malformed, *unsigned*, *about another document* or *different* worker (other release or other rule-set
fingerprint) means "not validated", never "valid". The worker refuses DOCTYPE/entity declarations, oversize, non-UTF-8 and
not-the-declared-root input and stops if a single byte of the pinned rule set changed. It serves only a caller holding the shared token
and signs every answer (HMAC-SHA256 over the document hash, release, rule fingerprint, engine, failures and the caller's one-time
nonce); an empty or non-SVRL report, or one in which no rule fired, is an error, never "no failures". Two keys are derived from the
shared token: the **bearer** the caller sends (so a captured request does not reveal the signing key) and the **signing key** itself.
The transport is plain HTTP inside the deployment, so the rule is: the worker is reachable only on an internal network (see the
checklist); the signature stops a forged answer, not eavesdropping.

| Layer | Where | Authority |
|---|---|---|
| L0 readiness (data completeness) | runtime, Python | NCollection |
| L1 arithmetic pre-check (reconciliation) | runtime, Python Decimal | our implementation of named official rules; never called "validated" |
| L2 official Schematron | the worker | **the official artifacts**; the only thing that sets `validated` |
| L3 the ASP | the ASP | the ASP |

Evidence (re-run with `satellites/einvoice_validator/e2e_check.py`, which needs no stack): all 30 official samples validate clean; 13
document types built by the engine validate clean (standard, credit note, volume-discount credit note, allowance + charge, exempt
allowance, zero-rated, exempt-only 480, mixed S/Z/E, services, out-of-scope seller without TRN, three-0.30-lines within slack, USD,
deemed supply); five negative controls fail with the right rule ids; the worker refuses DOCTYPE/entity and a tampered rule set;
~25–60 ms per document (one-off measurement).
Running the real end-to-end check found four defects in the engine's first draft (gross price on every line, `ibr-151-ae`, the
predefined-endpoint buyer identifier, a mis-scoped TIN advisory) which are fixed and tested.

**Two profiles, two workers (Cycle 3).** The Self-Billing rule set runs in a SECOND instance of the same worker image, mounted
with `resources/pint_ae_sb/1.0.4` (`einvoice-validator-sb` in `docker-compose.einvoice.yml`, same hardening, same internal
network). The engine picks the worker by the document's profile (`SpecProfile.validator_url_env`: `NC_EINVOICE_VALIDATOR_URL`
for Billing, `NC_EINVOICE_VALIDATOR_SB_URL` for Self-Billing), and the client checks the answering worker's rule-set
fingerprint against THAT profile's manifest, so a Billing worker can never validate a self-billed document (or the reverse):
the call is "unavailable", never "valid". No worker code changed. `e2e_check.py` (2026-10-29, ALL EXPECTATIONS HOLD) adds: both
official Self-Billing samples validate clean; an engine-built 389 and 261 validate clean; a 389 is refused by the Billing rules
and a 380 by the Self-Billing rules (`ibr-cl-01`); a 389 without the supplier VAT identifier fails `ibr-134-ae`, a 261 without
its UUID fails `ibr-193-ae`; and a Self-Billing answer offered as a Billing one is rejected by the client.

**Licence (recorded 2026-10-22):** SaxonC-HE is **MPL-2.0** per Saxonica's terms page (open-source Saxon-HE) and product page (some Apache-2.0 /
BSD-style components; "incompatible with secondary licenses"). The `saxonche` 13.1.0 wheel (sha256 `edf6ff3c…408c`) ships **no LICENSE file**
and declares none in its metadata. It runs unmodified as a separate package in its own container, never linked into Odoo or this
repository's code. Open item: collect Saxonica's notices before distributing the worker image (`satellites/einvoice_validator/LICENSES.md`).

### Validator worker: production-readiness checklist

Status as of this PR. **ready** = built and proven; **deployment** = a property of how it is run, documented here, to be created when
the worker is wired into a stack (no compose service is added by this PR, so the shared dev stack is untouched).

| Requirement | Status | Evidence |
|---|---|---|
| SaxonC-HE version pinned | ready | `requirements.txt`: `saxonche==13.1.0` |
| Install is hash-verified | ready | the same file lists the sha256 of the cp312 manylinux x86_64 and aarch64 wheels; the Dockerfile installs with `--require-hashes --only-binary=:all:` |
| Base image pinned / image reproducible | ready | `FROM python:3.12-slim@sha256:57cd7c3a…`; one pinned dependency; no other layer input (`worker.py`, `LICENSES.md`) |
| PINT-AE resources pinned and checksum-verified | ready | `resources/pint_ae/<release>/MANIFEST.txt`; the worker verifies every listed file at start and refuses to start on one changed byte; `.gitattributes` marks them `-text` so no checkout converts line endings |
| Licence / notice files | **open** | `LICENSES.md` is copied into the image; Saxonica's own notices bundle must still be collected before the image is distributed outside NCollection (the wheel ships none) |
| Needs no Odoo database; no tenant data access | ready | stdlib + SaxonC only, no DB driver, no Odoo import; stateless, one request never sees another's document |
| No unnecessary write access | ready | proven under `--read-only` rootfs; writes nothing; rules mounted `:ro` |
| Non-root, no capabilities | ready | `USER 65534`; proven with `--cap-drop ALL --security-opt no-new-privileges` |
| Internal and authenticated only | ready (auth) / deployment (network) | derived bearer on `POST /validate` (constant-time compare; token ≥ 32 chars, refuses to start without); answers signed with a second derived key and bound to the document and the request's nonce; run on a Docker network created `--internal` with no published port (proven). Plain HTTP is acceptable only on that internal network. |
| Malformed or unauthorised requests fail closed | ready | 401 without the token, 400 for bad JSON / base64 / DOCTYPE / ENTITY / non-UTF-8 / not-well-formed / wrong root, 411/413 for a missing, negative or oversized length, 503 when busy — all tested |
| Credentials never in logs | ready | the log line is `METHOD path` only; the token and bodies are never written (checked in the container smoke test) |
| Validator and resource versions recorded with each result | ready | the history row of every pass / failure carries engine, release and the rule-set fingerprint; findings carry the release |
| Health / readiness defined | ready | `GET /health` (open; release + fingerprint only). The worker listens only after verifying the rules and compiling the stylesheets, so a healthy container is one that holds exactly the pinned rule set; Docker `HEALTHCHECK` uses it |
| Timeouts and failures fail closed | ready | client: 20 s per socket operation, no redirects, size caps, every exception (including odd JSON) ⇒ "unavailable"; worker: 15 s socket timeout, 8 concurrent *authenticated* requests (an unauthenticated connection is answered 401 and is bounded only by the socket timeout and the network placement), 30 s wait for the single Saxon slot ⇒ 503; UTF-16 / NUL / non-UTF-8 documents are refused before the DOCTYPE scan |
| The engine never treats "unavailable" as validated | ready | tested at the client (`test_validator_client`) and at the model (`test_prepare`): state stays `locally_checked`, the reason is recorded and returned |
| SaxonC outside the Odoo image | ready | the Odoo image is unchanged |
| A transform that runs forever | **deployment** | Saxon cannot be interrupted in-process; run with container CPU / memory limits (`--memory 512m --cpus 1`) and rely on the client timeout. Moving validation to a queued job (OCQ, `queue_job`) is a recorded follow-up |

Run (the container smoke test used exactly this):

```
docker build -t nc-einvoice-validator satellites/einvoice_validator
docker run --rm --read-only --cap-drop ALL --security-opt no-new-privileges --network <internal-network> \
    -v <repo>/custom_addons/ncollection_uae_einvoicing/resources/pint_ae/<release>:/rules:ro \
    -e NC_EINVOICE_SPEC_VERSION=<release> -e NC_EINVOICE_VALIDATOR_TOKEN=<32+ chars> --memory 512m --cpus 1 nc-einvoice-validator
```

The Odoo container needs `NC_EINVOICE_VALIDATOR_URL` and the same `NC_EINVOICE_VALIDATOR_TOKEN` in its environment (never in a database).
With either missing, every validation is "unavailable" and no document can become `validated` — by design.

**Real documents through the official rules (R57).** `tests/test_real_move_crosscheck.py` builds REAL posted Odoo documents
(tax invoice 380, tax credit note 381, out-of-scope commercial invoice 480, exempt-only 480, zero-rated 380, domestic reverse
charge, an export scenario with its deliver-to address, and a negative case: the same mapper output with its UUID removed),
runs the engine's own preparation and XML generation, and, with `NC_EINVOICE_EXPORT_DIR` set, writes each XML with its
expected outcome. `e2e_check.py` (`NC_EINVOICE_FIXTURES`) validates exactly those files under the official rules and fails
when any required case is missing. CI runs both in the `test` job, so a mapper change the official rules reject fails the pull
request.

**Development and CI (no production change).** `make einvoice-e2e` runs the whole official path from a clean checkout with no
stack and no database (official samples, engine-built documents, negative controls, worker security checks, and with
`EINVOICE_FIXTURES=<dir>` the XML exported from real posted invoices). `make einvoice-validator-up` starts the worker beside
the dev Odoo through `docker-compose.einvoice.yml`: an `internal: true` network (no gateway, no egress), no published port,
read-only root filesystem, all capabilities dropped, `no-new-privileges`, 512 MB / 1 CPU, rules mounted read-only, token from
`.env` (`NC_EINVOICE_VALIDATOR_TOKEN`; the overlay refuses to start without it). No production compose file layers it, and
deploying the validator to production is a separate decision.

## 8. Credentials

No reversible secret store existed (inbound keys are hash-only). The engine reuses the repository's pattern — a master key from the
container environment (`NC_EINVOICE_SECRET_KEY`, never in a database) with a per-tenant derivation — as HKDF-SHA256 → AES-256-GCM
(`cryptography`, already in the image). The key and the authenticated data bind the tenant database, company, provider, secret name and
version (as a JSON list, so no name can be mistaken for two fields), so a copied row is useless elsewhere. The master key must be at
least 32 characters. A manager sets a secret through `set_secret` (a **Python API**: this phase ships no screen for it) and sees only
"configured / by / when"; a manager has read and delete rights on the secret rows but **cannot create or edit one**, and the identity
fields (company, provider, name, version, who, when) are immutable even to the service account, because changing one makes the row
undecryptable. The ciphertext column is readable only by the service account, which alone can decrypt (a private method, not RPC).
Secrets never enter XML, logs, history, audit rows (the audit seeder excludes the model), exception messages or the frontend (tests
prove each). No fingerprint of a secret is stored. The one `sudo()` of the credential path is in `set_secret`, after an explicit manager
check. Unset master key ⇒ the feature refuses; it never falls back to plaintext. The service account is created by the module as an
INACTIVE user (`data/transport.xml`), so it cannot log in.

**The credential screen (Cycle 4).** Provider form > Credentials (managers only): name, configured, key version, who and when — never
a value. *Set a credential* opens a write-through wizard: the value is handed to `set_secret` inside the wizard's `create` and is never
stored, not even in the transient table (tested by reading the column).

**Key rotation (R48, Cycle 4).** The master is VERSIONED. The deployment holds the current master and, only during a rotation, the
previous one, all in the container environment:

| Variable | Meaning |
|---|---|
| `NC_EINVOICE_SECRET_KEY` | the current master (≥ 32 characters) |
| `NC_EINVOICE_SECRET_KEY_VERSION` | its version (default 1, so every existing ciphertext keeps working) |
| `NC_EINVOICE_SECRET_KEY_PREVIOUS` | the previous master, only while rotating (must differ from the current one) |
| `NC_EINVOICE_SECRET_KEY_PREVIOUS_VERSION` | its version (required with it; must differ) |

Each ciphertext records its version, which is in both the key derivation and the AAD, so a ciphertext opens only with the master of its
own version. *Rotate credential keys* (managers; `ncollection.einvoice.secret.action_rotate_keys`) re-encrypts every credential of the
manager's own companies: decrypt with the master of its version, encrypt with the current one, swap — **one savepoint per
credential**, so each swap is atomic and a credential that cannot be decrypted (its master is missing, or the ciphertext was moved or
tampered with) keeps its old ciphertext and version and is only counted. Nothing leaves the method but counts; no value, key or
ciphertext is logged by it (Odoo's own DEBUG SQL log can show a ciphertext as a query parameter — the stored value, useless without the
master). New secrets are always written with the current version. Runbook:

1. generate a new master (`openssl rand -hex 32`); deploy it as `NC_EINVOICE_SECRET_KEY` with `…_VERSION` = old + 1, and move the old
   one to `…_PREVIOUS` / `…_PREVIOUS_VERSION` (every container that runs e-invoicing code: web workers and `einvoice-runner`);
2. in each tenant, a manager runs *Rotate credential keys* (or the operator calls `action_rotate_keys` per database); check
   `failed = 0`;
3. remove `…_PREVIOUS*` and restart. A credential still on the old version then fails closed (never silently) and is set again.

Tests (`tests/test_key_rotation.py`, 12): key-ring rules; old → new migration readable during and after; idempotent second run; the
rotated ciphertext refuses the old master, a wrong master and a wrong version; a missing master and a moved ciphertext leave the old
secret untouched and rotate the rest; another company's credential is untouched; only a manager rotates; no secret in logs or result;
the wizard stores nothing in clear.

## 9. States, history, immutability

Five independent dimensions — `local_state`, `submission_state`, `exchange_state`, `fta_state`, `business_state` — each with a table of
legal edges; a headline is derived, never stored as "Sent". Every transition appends a history row (actor, time, event, detail; a
validation row names the engine, release and rule-set fingerprint).

**The engine channel.** Only the engine writes state, identity, snapshots, references and archive links, and it does so as `sudo()`
(`env.su`) — a fact no RPC caller can supply. A second class of forgery was caught by the re-review: Odoo fills every field a create
did not supply from `default_<field>` **context keys**, which a client can send, so every create that has a whitelist (the e-invoice,
the provider, the legal hold) runs in a context stripped of the defaults a user may not type, and the archive-file check also inspects
the created record. An earlier draft keyed this on a context flag, which any client could send; the security
review caught it. For everyone else: `create` and `write` accept a **whitelist** (the per-document PINT data a user types, plus the
retention class and anchor for a manager, logged), anything else is refused; the history and the findings can be created only by the
engine (no `create` or `unlink` right for any group); the history is append-only even for `sudo`, while the findings of one layer are
replaced when that layer runs again (so each failed validation also writes the failed rule ids into its history row); line toggles can be neither added nor changed
once the document is checked, and the data a user types is frozen once the document is checked (prepare it again to edit). The
company is always the accounting document's company.

**Immutability.** The XML attachments cannot be modified, deleted, made public or given a token, and only the engine can put a file in
the archive; an e-invoice record is **never deleted**; a record leaving `not_submitted` (only from `validated`) becomes `locked` (content,
identity and XML frozen) and its accounting document can no longer be reset to draft or cancelled; the correction path is a credit note
or a new invoice (MD 243 Art 6(2)). **Before submission** a document is not stranded: preparing it again *supersedes* the generated XML —
the file stays in the archive for good, the history keeps the link, the record points at the next one. Validation that cannot be
performed is *recorded and returned*, never raised (an exception would roll the log back).

**Licence mirrored at the ORM.** `ncollection_core`'s licence enforcement never gates `ncollection.*` models by name, so the engine
checks the tenant's licence itself: it **fails closed** (a workspace whose licence does not name `ncollection_uae_einvoicing` can create
or run nothing), unlike the product's generic enforcement for an empty list.

## 9a. Transport (models/transport.py, controllers/callback.py)

**Dimensions.** Seven now: the five above plus `mls_state` (Peppol Message Level Status AB / AP / RE: about the *message*) and a
real `business_state` (the buyer's Invoice Response: AB, IP, UQ, CA, AP, RE, PD; `rejected` and `paid` are final). The TDD / FTA
confirmation stays on `fta_state`. None of the transport dimensions can touch another.

**One submission per revision.** `ncollection.einvoice.submission` stores the contract's idempotency key (sha256 of UUID,
revision and XML digest) under a database `UNIQUE`, and a stable correlation id. The provider receives the same key on every
attempt, so a timeout after a successful send is resolved by sending again: the contract makes the provider answer with the same
document id (proven: `test_a_timeout_after_a_successful_send_creates_no_second_document`).

**Workers.** The job locks its row (`FOR UPDATE NOWAIT`); a second worker that finds it locked, or a job that runs before its time
or after the work is done, does nothing. A worker that dies mid-send leaves a lease that expires (10 min); the enqueue cron puts
it back with the same key.

**Retries.** TRANSIENT only, `lib/backoff.py`: 60 s doubling, capped at 6 h, deterministic ±20 % jitter, Retry-After as a floor;
after 8 attempts the submission is a dead letter (`dead`, `permanent_failure`). AUTHENTICATION / CONFIGURATION: `attention`, no
retry until a manager restarts it. REJECTED: final, with an `asp` finding naming the provider's code. An adapter that raises is a
transient failure, never a crashed job. An answer whose correlation or key is not the submission's is refused.

**Events.** Callbacks (`POST /ncollection/einvoice/callback/<provider id>`, no session, verified by the adapter before parsing,
1 MB cap, the same 401 for every refusal) and polls store each event once (`UNIQUE(provider, message id)`), the payload's sha256
and never the payload. An event that arrives before the acceptance it needs waits (`pending`) and is applied once the document
gets there; one that conflicts with a final state is kept and `ignored`; one whose ids match no submission, or whose correlation
and document ids disagree, is an `orphan` — never guessed.

**Topology.** Jobs run on the `einvoice` queue_job channel; the cron only enqueues (DESIGN_CRON_AND_QUEUE_TOPOLOGY.md §2). The
adapter runs as `einvoice-service@ncollection.internal`, created **inactive** (it cannot sign in and holds no seat), the only
identity that can decrypt a provider credential. Tenant-side execution: §9b.

## 9b. The tenant runner (runner_scope.py, docker-compose.einvoice-runner.yml)

**Decision (2026-10-29): ONE shared runner, not one per tenant.** OCA's jobrunner already serves several databases and elects
itself master of each with a per-database advisory lock; what it lacked was a *scope* and a *refresh* (it serves `db_name` or
every database, discovered once at start). `einvoice-runner` is the stock `odoo:19` image with this module in `--load` and
`NC_EINVOICE_RUNNER=1`; the module's `post_load` then patches the runner class in that process only:

* **Scope.** A database is served only if `runner_scope.decide()` says so: the release gate calls it a TENANT (never the platform:
  the gate keys that on `ncollection_saas`), its gate state is READY (provisioning, upgrading, held, behind or failed databases run
  nothing), `ncollection_uae_einvoicing` and `queue_job` are installed, and the workspace status the platform pushes is not
  suspended / expired / cancelled. A database that cannot be read is not served that round.
* **Refresh.** Every 60 s the eligible set is re-read; joiners are added with their pending jobs, leavers are removed from the
  channel manager and closed. New, suspended or deleted tenants need no restart.
* **Execution and isolation.** Unchanged OCA mechanics: the runner calls `/queue_job/runjob?db=<db>` on its OWN HTTP server
  (`127.0.0.1`, no published port, `--db-filter=.*` because the scope, not the filter, chooses the databases), which runs the job in
  that database's registry and transaction. Credentials are per database / company / provider and decrypted only by the inactive
  service user of that database. The release gate is loaded (`ncollection_core` in `--load`), so a database that stops being READY
  answers `runjob` with its maintenance response even between two refreshes.
* **What stays as it was.** Without `NC_EINVOICE_RUNNER=1` the patch is a no-op: the platform's `provisioning-runner`, the HTTP
  workers and every other process keep OCA's behaviour. `--max-cron-threads=0`: crons stay where they are (invariants R6).
* **Edge.** `/queue_job/runjob` is `auth='none'` and must never be reachable from the public edge. The dev edge now answers 403 for
  `/queue_job/` (nginx `ncollection.dev.conf`). **Release item: the production edge (`ncollection.prod.conf`) needs the same block;
  it is not changed in local development.**
* **Not deployed.** The overlay is opt-in; production adopts it in the final release sequence.

## 9c. Inbound e-invoices (models/inbound.py, lib/inbound_parse.py)

An inbound document becomes at most a **DRAFT** vendor bill (decided 2026-10-28): never posted, never validated, never paid by the
engine, and nothing is guessed.

**Path.** An `inbound` event (callback or poll, deduplicated like every event) enqueues a fetch; the fetch job calls the adapter's
`fetch_inbound`, checks the provider's digest against the bytes, archives the XML (the legal artefact, kept as a protected
attachment with its sha256; transport events keep only digests) and records ONE intake row; processing then runs the states:

```
received -> validating -> matching -> ready -> bill_created
validating -> validation_failed | rejected | duplicate | review | (validator unavailable: stays validating, retried with backoff)
matching   -> unmatched (no supplier / identifiers disagree)        ready -> review (tax, currency or document amounts)
unmatched -> matching, review -> validating                         (Process again, after the master data is corrected)
```

The inbound `state` is its own dimension: it never touches MLS, the business response, `fta_state` or any outbound state.

**Validation, in order.** Integrity (the archived bytes against their digest); safe parsing (no DTD, entities or network; a
DOCTYPE is refused outright, which is the XXE defence); only 380 / 480 / 381 / 81 are purchase documents, and 389 / 261 are
self-billed (below); the BUYER must be this company by TRN or participant id (TIN), never by name, else `rejected`; the engine's own preconditions (number, date, currency, a supplier
identifier, complete lines, a credit note's billing reference, line total = Σ lines, tax-exclusive = lines − allowances +
charges, tax-inclusive = exclusive + tax, payable = inclusive − prepaid, at 0.01); then the OFFICIAL PINT-AE rules on the
isolated validator (§7), whose failed rule ids become `OFFICIAL:<id>`.

**Supplier matching.** TRN, then participant id (TIN, which is the endpoint id on the UAE scheme), then legal registration
(`company_registry`), among active partners shared or of this company. Every identifier the document carries must point to the
SAME commercial partner: none → `unmatched` (no partner is ever created), more than one → `unmatched` (ambiguous or conflicting).
A name never decides; a renamed supplier with the same TRN still matches.

**The draft bill.** `in_invoice` / `in_refund` of the provider's company, the matched partner, `ref` = the document number,
invoice and due dates, the active currency of the document (else `review`), one line per document line with the line amount as
the truth (unit price = line amount / quantity, so line discounts are carried exactly), and the purchase tax that the company's
**tax map** gives for the line's PINT category and rate — exactly one, else `review`. Document-level allowances, charges or prepaid
amounts go to `review`. The bill is created inside a savepoint and kept only if its untaxed and tax totals equal the document's
(0.01); otherwise `review`. `account.move.nc_inbound_id` links the bill to its source, and the narration names the provider and
its document id.

**Duplicates, enforced by the database.** `UNIQUE(provider, asp document id)` and `UNIQUE(provider, message id)` collapse repeated
callbacks, polls, retried fetches and callback-versus-poll races into one row; a partial unique index on the DOMAIN identity
(company, supplier identifier, kind, number) over non-duplicate rows makes the same invoice delivered under a new provider id a
`duplicate` row pointing at the original; `account.move.nc_inbound_id` is unique, so one intake row has at most one bill. Each
processing step locks its row (`FOR UPDATE NOWAIT`) and is idempotent.

**A received SELF-BILLED document (389 / 261, Cycle 3).** The customer issued it for this company, its supplier, so the roles
mirror: the SELLER must be this company (else `rejected`, `NCOLLECTION:inbound_wrong_recipient`; a 389 that names this company
as its BUYER is refused the same way); it is validated with the **Self-Billing** rule set; the counterparty matched (same
identifier rules) is the CUSTOMER; it proceeds only if an active `receive` self-billing agreement with that customer covers the
issue date (else `review`, `NCOLLECTION:inbound_self_billing_agreement`); and it becomes a **DRAFT customer invoice / credit
note** (`out_invoice` / `out_refund`) with the company's SALE tax map. Same duplicate layers (the domain key uses the
customer's identity). Cycle 2's "389 / 261 → review" stop is replaced.

## 9d. Self-billing (PINT AE Self-Billing: 389 / 261)

Guidelines V2.0 §10.3 (the only official text on it): "the buyer may issue the Electronic Invoice on behalf of the supplier"
**subject to an agreement** between them; self-billing "only appl[ies] for VAT purposes" and "is not available for suppliers who
are not registered for VAT", so "there is no option for self-billing of Commercial Invoices"; it "will require the buyer to be
on the Electronic Invoicing System"; and a self-billed Tax Invoice "should fulfil the criteria" of a Tax Invoice.

**The workflow.** A posted vendor bill (`in_invoice`) or vendor refund (`in_refund`) → its e-invoice (`self_billing` set by the
ENGINE from the document type; a checkbox never makes a document self-billed) → preparation on the Self-Billing profile → XML
with the Self-Billing `CustomizationID` / `ProfileID` and type 389 / 261 → official validation by the Self-Billing worker → the
SAME transport engine as every e-invoice (queue, idempotency key, lease, retry, dead letter) with `SubmissionRequest.self_billed`,
`customization_id` and `profile_id` set (a self-billed document travels from the BUYER, this Person, to the SELLER: an adapter
must not infer direction from the party roles) → provider response → MLS / FTA (TDD) confirmations STORED from the ASP (never
generated) → final state, append-only history and the immutable XML archive.

**Eligibility, deterministic (`ncollection.einvoice.self.billing.agreement`, models/self_billing.py).** An e-invoicing MANAGER
records the agreement (partner, reference, period, direction `issue` = we self-bill our supplier / `receive` = our customer
self-bills us) and activates it; activation is refused when the supplier is not VAT-registered (no valid TRN) or another active
agreement for the same partner and direction overlaps; an active agreement cannot be edited (terminate it and record a new one),
termination records who and when, and a once-active agreement is never deleted. Creating a vendor bill's e-invoice is refused
without ANY active `issue` agreement with that supplier; every preparation then checks, and blocks with its own rule:

| Rule | Condition |
|---|---|
| `NCOLLECTION:self-billing-agreement-missing / not_yet / expired / ambiguous` | exactly one active agreement must cover the transaction date |
| `NCOLLECTION:self-billing-supplier-not-registered` | the supplier (the document's SELLER) has a valid TRN |
| `NCOLLECTION:self-billing-buyer-not-on-system` | this company (the BUYER) has a valid TIN (its participant id) and is not marked "not onboarded" |
| `NCOLLECTION:self-billing-commercial-not-allowed` | at least one VAT-relevant line: a self-billed document stays 389 / 261 and is never turned into 480 / 81 |
| `NCOLLECTION:self-billing-export-not-applicable` | no export flag: under self-billing the buyer is this UAE company on the system |
| `NCOLLECTION:self-billing-ibr-055-ae / -ibr-158-ae / -ibr-124-ae` | a 261 references its original (except VD), carries its reason and no VAT point date (see `self-billing-credit-rules-not-applied`, §3) |

**Mandatory data, field by field.** Self-Billing has the same data model as Billing (stated on the official package page), so
every mandatory field and fatal rule of §4–§6 applies unchanged, with the parties as the document defines them: the SUPPLIER is
`AccountingSupplierParty` (its TIN endpoint `0235:<TIN>`, its TRN — `ibr-134-ae`/`ibr-177-ae`, its legal registration —
`ibr-150-ae`, its address) and THIS company is `AccountingCustomerParty`. The payee is the supplier: payment means come from
the bill's own (supplier) bank account, never this company's default account (`ibr-192-ae` blocks a credit transfer without
it). Credit notes reference the original self-billed invoice through Odoo's reversal link (§9e).

| Field | Source | Code | Validation | XML | Test |
|---|---|---|---|---|---|
| CustomizationID / ProfileID | PINT AE SB 1.0.4 | `spec.PROFILES['pint-ae-sb-1.0.4']` | official `ibr-cl-*`, profile fingerprint | `cbc:CustomizationID`, `cbc:ProfileID` | `test_a_self_billed_tax_invoice_389` |
| Type 389 / 261 | UNCL1001 SB lists; G2.0 §10.1 | `lib/doctype.py` | `ibr-cl-01` (SB worker) | `InvoiceTypeCode` / `CreditNoteTypeCode` | 389 / 261 tests; e2e `ibr-cl-01` both ways |
| Seller = supplier (name, TIN endpoint, TRN, legal id, address) | G2.0 §10.3; MF1.1 | `prepare._nc_parties` | `ibr-134/150/177-ae`, readiness | `AccountingSupplierParty` | 389 test (supplier block), e2e `ibr-134-ae` negative |
| Buyer = this company | G2.0 §10.3 | `prepare._nc_parties` | `buyer-not-on-system`, `ibr-135/149-ae` | `AccountingCustomerParty` | `test_the_buyer_must_be_on_the_einvoicing_system` |
| Agreement | G2.0 §10.3 | `models/self_billing.py` | `self-billing-agreement-*` | not in XML (stored on the record, in the payload) | eligibility tests (8) |
| Payment account | MF1.1; `ibr-192-ae` | `prepare` (no company fallback) | `ibr-191/192-ae` | `PaymentMeans` | `test_the_company_default_account_is_never_the_suppliers` |
| Original invoice reference, reason | VAT DL Art 70; `ibr-055/158-ae` (extended) | `_nc_original_facts` | `self-billing-ibr-*`, `credit-note-original-*` | `BillingReference`, `DiscrepancyResponse` | 261 tests |
| Lines, quantities, prices, discounts, allowances, charges, totals, tax | Billing model | unchanged mapper | reconciliation + official | unchanged | 389 / 261 official validation (e2e, real-move fixtures) |

## 9e. Credit notes (381 tax / 81 commercial / 261 self-billed)

The three kinds stay distinct: a **tax credit note** 381 corrects a tax invoice 380, a **commercial credit note** 81 a commercial
invoice 480, a **self-billed tax credit note** 261 a self-billed 389 (`prepare.CREDIT_PAIRS`). The type follows the credit note's
own lines (`lib/doctype.py`), and the pair is then checked against the original's e-invoice.

**The reference.** The ONLY reference is Odoo's own reversal link (`reversed_entry_id`): never a typed value, so it can neither be
ambiguous nor point outside this database (a tenant is a database). `_nc_original_facts` checks it and readiness blocks with:
`credit-note-original-not_found / other_company / wrong_kind` (a customer credit note corrects a customer invoice, a vendor refund
a vendor bill) `/ other_partner / not_posted / not_einvoiced` (the original HAS an e-invoice that never passed the official
validation) `/ type_mismatch / other_profile`. An original with NO e-invoice at all (issued before the mandate, out of scope,
or before this engine) is allowed with a WARNING (`credit-note-original-without-einvoice`), silent when the original predates the
company's configured e-invoicing start. A credit note without a link needs the volume-discount reason (`ibr-055-ae`).

**Amounts (VAT Decree-Law Art 70; Guidelines V2.0 §12.2 #3–#4).** Partial credit notes are allowed. Every POSTED credit note of
the same original, this one included, may credit at most the original's total AND at most its VAT (`credit-exceeds-original`,
checked separately, so a credit carrying VAT a zero-rated original never charged is refused even within the amount).

**One credit note for several invoices — formal review (Cycle 4).** Guidelines V2.0 §12.2 #3: "In instances where a single electronic
Tax Credit Note is issued for multiple past electronic Tax Invoices, references to these multiple electronic Tax Invoices **can be
provided** in one electronic Tax Credit Note." That is a PERMISSION, not a mandate; fatal `ibr-055-ae` requires *a* preceding
reference (IBG-03), which one original satisfies; nothing in PINT-AE 1.0.4, the Mandatory Fields V1.1 or MD 243 requires a Person to
consolidate credits. Issuing one credit note per invoice is always compliant. Supporting it would need a second, non-accounting
reference list whose amounts Odoo's reconciliation (one `reversed_entry_id`) would not see, which is exactly the inconsistency the
original-amount check exists to prevent. **Decision: intentional supported-scope restriction, status NOT APPLICABLE (not a
requirement).** Such a document cannot be produced, so it is never sent wrongly; a customer who wants it is told to credit each invoice.

**Lifecycle.** A credit note has no shortcut: draft → prepare → XML → official validation → queued submission → ASP → MLS / FTA
→ final state → archive, exactly as an invoice. One e-invoice per credit-note revision (`unique(move_id, revision)`); a second
submission of the same revision is refused (`already submitted`) and the idempotency key is unique.

| Case | Test (tests/test_credit_notes.py unless noted) |
|---|---|
| full reversal 381 (amounts, VAT, reference, payable, XML) | `test_a_full_reversal` (+ official, `real-credit-note-381-full`) |
| one-line partial | `test_a_one_line_partial_credit` (+ official) |
| multi-line partial quantity | `test_a_multi_line_partial_quantity_credit` (+ official) |
| partial amount | `test_a_partial_amount_credit` |
| partial tax adjustment | `test_a_partial_tax_adjustment` |
| commercial 81 for a 480 | `test_a_commercial_credit_note_81_for_a_480` (+ official) |
| self-billed 261 full | test_self_billing `test_a_self_billed_tax_credit_note_261_full_reversal` (+ official SB) |
| missing original / other company / other customer / wrong kind / not posted / original never validated / type mismatch | `TestCreditNoteReferences` (7 tests) |
| excessive amount / excessive VAT | `test_credits_beyond_the_original_amount_are_refused`, `test_credited_vat_beyond_the_original_vat_is_refused` |
| duplicate credit note | `test_a_credit_note_has_one_einvoice_per_revision`; transport idempotency (test_transport, test_self_billing) |
| cross-tenant | structural: database per tenant and the ORM link is the only reference; not separately testable inside one database |
| invalid VAT / invalid totals | reconciliation + official rules (test_pint_logic, test_prepare; unchanged for credit notes) |

## 9f. ASP research and the A-Cube adapter (Cycle 4, 2026-10-29)

**Landscape (authoritative source).** The MoF page *eInvoicing Accredited Service Providers (ASPs)*
(`mof.gov.ae/en/about-us/initiatives/einvoicing/einvoicing-accredited-service-providers-asps/`, fetched 2026-10-29) lists 69 accredited
providers with accreditation numbers. Third-party counts (41 / 54 / 70) disagree; only the MoF list is used. No commercial commitment,
no purchase, no credential requested.

| Candidate (MoF no.) | Public API contract | Sandbox | PINT-AE XML in | Self-billing | TDD | Webhook security | Fit |
|---|---|---|---|---|---|---|---|
| **A-Cube** (188961) | OpenAPI 3.1 `docs.acubeapi.com/openapi/gov-ae-api.json` | documented host `ae-sandbox.api.acubeapi.com`; account registration | yes (`application/xml`) | yes (`self-billed-invoice/-credit-note`, 389/261) | `GET …/{uuid}/tdd` | static token only | **technical target** |
| Aigentrix / TronStride (182493) | public REST reference | `environment=SANDBOX` per request; account + API key | yes (`create-from-xml`) | not documented | FTA `taxStatus` + PDF renditions | HMAC-SHA256 (`X-Aigentrix-Signature`, timestamp) | runner-up |
| Complyance (112219) | partial (GETS JSON mapping) | request-based | JSON-first | linked, not specified | portal downloads | not documented | weak for an XML engine |
| Avalara (134809) | SDK; UAE specifics not public | account-gated | UBL via mandate | not documented | not documented | not documented | gated |
| Pagero / ecosio / EDICOM / others | marketing / gated | gated | — | — | — | — | not assessable publicly |

**Why A-Cube:** the only accredited ASP whose UAE contract is fully PUBLIC and machine-readable, which takes our exact artefact (PINT AE
UBL XML) unmodified, names self-billing explicitly and exposes the TDD per document. **What it lacks:** signed webhooks (a static
token) and a published idempotency key; both are handled by design below.

**The adapter (`ncollection_uae_einvoicing_acube`).** Behind `AdapterBase`; the core imports nothing from it. Built against the
published contract (OpenAPI sha256 `858d1060…ddff`); **never run against the provider** — no credential exists.
* Auth: `POST common.api.acubeapi.com/login` (`email`, `password`, `environment`) → 24 h JWT, cached in memory only, renewed
  10 min before `exp` and once on a 401. Secrets `email` / `password` / `webhook_token` come from the engine's encrypted store.
* Outbound: 380 / 480 / 389 → `POST /invoices/outgoing`; 381 / 81 / 261 → `POST /credit-notes/outgoing`; 201 = accepted (`invoiceId`
  stored as `<collection>:<uuid>`), 400 / 422 = REJECTED (the violation CODE kept, never its text), 404 = CONFIGURATION (legal entity
  not onboarded), 401 / 403 = AUTHENTICATION, 429 / 5xx = TRANSIENT with `Retry-After` honoured. Self-billed documents are owned by
  the BUYER (this Person), which the request now says (`self_billed`).
* Idempotency (no key in the API): before every POST the adapter looks for an outgoing document of the owner TIN with the same number
  and type (paged, ≤ 5 pages) and returns it as `duplicate`; a created-but-unreadable answer is TRANSIENT so the retry looks first.
* Status: `GET /{collection}/{uuid}`; only `delivered` (exchange delivered) and `delivery_failed` (exchange rejected) are mapped —
  `accepted`, `rejected`, `withdrawn`, `action_required` have NO published meaning and are stored as unmapped events, moving no state.
  `GET …/tdd` 200 ⇒ an `fta reported` event with `tdd_reference = acube-tdd-sha256:<sha256>`. The engine never generates a TDD.
* Callbacks: A-Cube webhooks carry only a static token (`X-NC-Webhook-Token`, ≥ 32 characters, constant-time compare). Because an
  unsigned body can be replayed or forged by whoever learns the token, **a callback is only a trigger**: `document.delivered` makes the
  adapter re-read the document from the API with our credentials; `document.received` announces an inbound id that the engine then
  fetches. Event ids are deterministic per (document, state), so a replay is a duplicate the engine drops. The engine's controller
  still applies its 1 MB limit and answers the same 401 for every refusal (no existence leak).
* Inbound: `GET /invoices|credit-notes?direction=incoming` (paged cursor), XML from `…/{uuid}/source`; an outgoing document is never
  accepted as inbound.
* SSRF: the two hosts are constants chosen by the provider's environment (sandbox / production); no URL comes from configuration or
  from an answer; redirects are not followed.

Tests (`ncollection_uae_einvoicing_acube/tests`, 34): the engine's adapter contract suite (its four signed-callback tests re-stated for
the trigger model), endpoint routing for all six types, buyer ownership of self-billed documents, token reuse / renewal, refused
credentials, configuration vs rejection, violation code, Retry-After, status mapping, TDD as stored confirmation, host not
configurable, inbound list / fetch, outgoing never inbound, no secret in a URL, token verification, body never trusted, replay
dedup, malformed / unknown callbacks. All against a SCRIPTED server that implements only the published contract — not provider answers.

**SANDBOX-VERIFY before any release (needs an A-Cube sandbox account):** the login flow and token claims; whether `cbc:UUID` is kept;
the real meaning of `accepted` / `rejected` / `withdrawn` / `action_required`; how MLS documents (`documentType: mls`) relate to an
outgoing document; the list endpoint's paging shape (`hydra:member` or array) and duplicate detection; the webhook payload fields;
legal-entity onboarding via EmaraTax; behaviour for predefined endpoints (98 / 97 / 99, report-only); 409 / duplicate answers.

### 9f.1 Adapter audit (Cycle 5) — defects found and fixed

| Finding | Severity | Fix | Test |
|---|---|---|---|
| The in-memory token cache was keyed by provider id + environment. The shared tenant runner serves many databases in ONE process, where two tenants' provider rows can have the same id: one tenant could have reused another tenant's A-Cube token | **HIGH (tenant confusion)** | key = (database, provider id, environment, sha256 of the login email) | `test_two_tenants_never_share_a_token` |
| The duplicate check matched on document number + type only; a number is not globally unique (another revision, another counterparty), so a legitimate document could have been suppressed | HIGH | a candidate must match owner TIN, direction, type, number AND recipient, and its stored XML (`/source`) must carry OUR `cbc:UUID` | `test_the_same_number_with_another_uuid_is_not_a_duplicate`, `…_for_another_recipient_…` |
| When the list endpoint failed, the adapter fell through and POSTed blind (a duplicate after a lost answer) | HIGH | the check fails SAFE: TRANSIENT `duplicate-check-inconclusive`, nothing sent, retried with backoff; also after 50 full pages | `test_an_unreadable_list_never_sends_blind` |
| No response-size limit; TLS verification implicit | MEDIUM | streamed read refused past 20 MB (`response-too-large`); `verify=True` stated; redirects still refused | `test_an_oversized_provider_answer_is_refused` |
| Inbound XML had no size cap before archiving | MEDIUM | 10 MB (the engine parser's limit), PERMANENT `inbound-too-large` | `test_an_oversized_inbound_document_is_refused_before_archiving` |

Timeout AFTER A-Cube stored the document, then a retry: one document at the provider, the retry returns it as `duplicate`
(`test_a_timeout_after_acceptance_then_a_retry_gives_one_document`). Callback and polling then name the same provider id, which the
engine's `UNIQUE(idempotency_key)` / one-submission-per-revision already keep as ONE logical submission.

### 9f.2 Identifiers — none is reused for another

| Identifier | Owner | Where stored | Notes |
|---|---|---|---|
| e-invoice UUID (`cbc:UUID`) | Balance Cloud (engine) | `ncollection.einvoice.uuid` | generated once per revision, locked at submission; required in the XML by fatal `ibr-193-ae` before any ASP sees it; also the adapter's duplicate proof |
| idempotency key | engine | `submission.idempotency_key` (UNIQUE) | sha256(uuid, revision, xml sha256) |
| correlation id | engine | `submission.correlation_id` (UNIQUE) | per submission |
| provider document id | A-Cube (`invoiceId`) | `submission.asp_document_id`, `einvoice.asp_reference` as `<collection>:<uuid>` | never written into `uuid` |
| provider event / message id | adapter (deterministic per document + state), or the provider's | `event.message_id` UNIQUE(provider, message_id) | replays collapse |
| MLS | ASP | `einvoice.mls_state` + event | A-Cube models MLS as separate documents (`documentType: mls`): **SANDBOX-VERIFY** how they link |
| TDD / FTA reference | ASP | `einvoice.fta_reference = acube-tdd-sha256:<sha256 of the TDD>` + event | the TDD is downloaded, digested and referenced, never generated |

**UUID authority, proved as far as the sources go.** PINT-AE: `ibr-193-ae` (fatal) — the document carries `cbc:UUID`. UAE: Guidelines
V2.0 §5.2 #7 gives *generating* it to the ASP, which cannot be what is validated, because the official rules run on Corner 1's
document. A-Cube's OpenAPI: the outgoing endpoint takes the XML verbatim and returns `invoiceId` (its own resource uuid); nothing says
it rewrites `cbc:UUID`. **Not provable without the sandbox:** whether A-Cube's stored `/source` still carries our UUID. The adapter
DEPENDS on that (duplicate proof), so the first sandbox test is exactly that assertion (§9f.4, step 3); if A-Cube rewrites it, the
duplicate proof must change before release.

### 9f.3 A-Cube status → engine (the whole published enum)

| A-Cube status | Engine event | State moved | Final? | Retry? | Evidence |
|---|---|---|---|---|---|
| (POST 201) | `submission accepted` | `submission_state = asp_accepted` | no | — | OpenAPI 201 |
| (POST 400 / 422) | `submission rejected` | `asp_rejected` | yes | no | OpenAPI 400 / 422 |
| (POST 404) | failed CONFIGURATION | submission `attention` (e-invoice `transient_failure`) | — | after a manager acts | OpenAPI 404 "legal entity not found" |
| (401 / 403) | failed AUTHENTICATION | submission `attention` | — | after a manager acts | login doc |
| (429 / 5xx / timeout) | failed TRANSIENT | submission `retry_wait` | — | yes, backoff + Retry-After | classification |
| `delivered` | `exchange delivered` | `exchange_state = delivered` | per dimension | — | OpenAPI enum + lifecycle guide |
| `delivery_failed` | `exchange rejected` | `exchange_state = rejected_by_buyer_asp` | yes | no | OpenAPI enum |
| `new`, `valid`, `sending_queued`, `sending_started`, `processed`, `received` | `exchange acube:<status>` | **nothing** (stored, ignored) | no | poll again | in-flight states |
| `sending_failed`, `accepted`, `rejected`, `withdrawn`, `action_required` | `exchange acube:<status>` | **nothing** (stored, ignored) | unknown | — | **no published meaning: SANDBOX-VERIFY** |
| anything not in the enum | `exchange acube:<status>` | **nothing** | — | — | safe fallback: an unknown status never becomes accepted |

### 9f.4 Sandbox: what is missing, and the activation procedure

**Blocker (external):** no A-Cube account. Checked (names only, no values printed): `.env`, `.env.example`, `config/odoo.conf` contain no
A-Cube or ASP variable. Required, per A-Cube's authentication guide: an A-Cube platform account (**email + password**) for the
**sandbox** environment, and for documents a **legal entity** onboarded in the sandbox (A-Cube's EmaraTax onboarding flow:
`POST /legal-entities/onboarding/verify` / `init`), i.e. a test TIN A-Cube accepts. Nothing is invented meanwhile.

Activation, once supplied (dev stack only):
1. install `ncollection_uae_einvoicing_acube` in a scratch tenant; provider *A-Cube*, environment *Sandbox*;
2. *Set a credential*: `email`, `password`, and `webhook_token` (≥ 32 random characters); register the event destination with
   `authType=header`, `authKey=X-NC-Webhook-Token` and that token (A-Cube API `POST /event-destinations`);
3. first assertion: send one 380, read `/source`, assert our `cbc:UUID` is unchanged (§9f.2);
4. then the matrix: 380 / 480 / 381 / 81 / 389 / 261 and the twelve scenarios from `tests/test_scenario_matrix.py`; rejection
   (a document the official rules refuse, sent deliberately); duplicate after a forced timeout; status polling; callback,
   callback replay, callback before / after the final state; inbound polling (needs a second sandbox entity sending to us);
   TDD download;
5. record every outcome as observed; a case the sandbox cannot produce is **NOT TESTABLE IN SANDBOX**, never "passed".

None of steps 1–5 has been executed: there is NO real-sandbox evidence yet. The full step-by-step execution pack (credentials, onboarding, 24 steps with expected states and evidence, questions for A-Cube) is `docs/markdown/UAE_EINVOICING_ACUBE_SANDBOX.md`. Diagnostics added in this pass: an A-Cube refusal now records WHERE it failed (the violation `propertyPath`, structure only, never the message text).

## 9g. Production deployment requirements (documented, NOT applied)

**Public `/queue_job/` block — a release change, and a pre-existing exposure.** `nginx/conf.d/ncollection.prod.conf` (the 443 server for
`.${DOMAIN}`) has NO `/queue_job/` location today, while queue_job already runs in production for the platform runner, so
`/queue_job/runjob?db=…&job_uuid=…` (auth='none') is reachable through the edge (low risk: it needs an unguessable job uuid; still
not acceptable). The exact change, beside `location /web/database` in the 443 server and in
`nginx/templates/tenant-custom-domain.conf.j2` (and every rendered `nginx/conf.d/tenants/*.conf`):

```nginx
    location ^~ /queue_job/ {
        return 403;
    }
```

then `nginx -t` and a reload, and a check that `curl -o /dev/null -w '%{http_code}' https://<host>/queue_job/runjob` answers 403. The
port-80 server only redirects and needs nothing. Not applied: production nginx is out of bounds this cycle.

**Services.**

| Service | Image / command | Network | Limits / policy |
|---|---|---|---|
| `einvoice-runner` (tenant queue) | odoo:19, `NC_EINVOICE_RUNNER=1`, `--load=base,web,queue_job,ncollection_core,ncollection_uae_einvoicing --workers=2 --max-cron-threads=0`, `ODOO_QUEUE_JOB_CHANNELS root:2,root.einvoice:2`, jobs called on its own `127.0.0.1:8069` | db plane + egress to the ASP hosts only; NO published port | `restart: unless-stopped`; 1 CPU / 1 GB as a start (measure); exactly ONE instance (OCA's per-database advisory lock makes a second one idle, not harmful); stop signal SIGTERM, grace ≥ `--limit-time-real` (600 s) |
| `einvoice-validator` (Billing) | `satellites/einvoice_validator`, rules `pint_ae/1.0.4` read-only | internal network, no gateway | read-only FS, no capabilities, 512 MB / 1 CPU, `restart: unless-stopped` |
| `einvoice-validator-sb` (Self-Billing) | same image, rules `pint_ae_sb/1.0.4` read-only | same | same |
| web workers / runner env | `NC_EINVOICE_VALIDATOR_URL`, `NC_EINVOICE_VALIDATOR_SB_URL`, `NC_EINVOICE_VALIDATOR_TOKEN`, `NC_EINVOICE_SECRET_KEY` (+ `_VERSION`, and `_PREVIOUS*` only while rotating) | — | secrets from the deployment's secret store, never in git |

Queue channel `root.einvoice` (data/transport.xml); crons only enqueue. Runner hardening added in Cycle 4: a database that vanishes or is
half-provisioned between the probe and the connect is skipped and retried, and an unreadable server keeps the current scope, so a
refresh never takes the loop down (`TestRunnerResilience`, 3 tests). Logs carry reasons, never database contents or secrets.

## 10. Retention and tenant offboarding

**Evidence export package (R46, Cycle 4).** Configuration > *Create Evidence Package* (e-invoicing managers; for their own companies
only) produces ONE ZIP per company and records who / when / sha256 on an append-only `ncollection.einvoice.export` row (the audit trail;
never edited, never deleted). Contents (`models/export.py`, format `lib/export_package.py`): `package.json` (database, company, TRN,
counts, what is and is not included), `MANIFEST.sha256` (sha256 of every file), and per e-invoice `record.json` (identity, the five
states + MLS + business, payload, findings, full history, submissions with attempts, provider events with TDD / MLS references, the
self-billing agreement) plus EVERY archived XML (superseded ones too) with its sha256; per received document its facts, history and
the received XML. Never included: credentials, provider message bodies (never stored). Deterministic (same evidence ⇒ same bytes);
`verify()` refuses traversal / absolute paths, duplicates, links, oversize, unlisted or changed files — safe on a package from
elsewhere. Elevation: `sudo()` only after the manager check and only for that company. Tests: `tests/test_export_package.py` (12).
Still NOT built (approved model below): the destructive purge.

**Facts.** MD 243 Art 11 as replaced by MD 197/2026 Art 2 (in force 1 Oct 2026): retain e-invoices, credit notes and associated data
for the period in the Tax Procedures Law, producible to the Authority; the former in-UAE storage requirement is removed. Guidelines
V2.0 §5.4 adds what "met" means (integrity preserved, promptly accessible to the FTA, retrievable in complete and readable form) and
that "associated data" is what supports integrity, authenticity and auditability, not general business documentation. The
Guidelines V2.0 (§5.4, App 4) summarise the periods as 5 years after the Tax Period for
Taxable Persons, 5 years from the end of the creation year for others, 7 years for real-estate records, with +4 years on a dispute,
audit or audit notification and +1 year after a voluntary disclosure; no storage location or layer is mandated; delegating storage to an
ASP is allowed but the legal obligation stays with the Person. The primary text (Cabinet Decision 74/2023 Art 3) has not been read:
**OCQ-12**.

**Implementation: fail-closed and data-driven.** Retention periods are rows of `ncollection.einvoice.retention.rule` with their citation
and a `confirmed` flag that starts false; an e-invoice is purgeable only with a confirmed matching rule, a set anchor date, no open
hold and every closed hold's extension elapsed — otherwise `retain_until` is empty (= not purgeable). No period is hard-coded and
**no purge exists in this phase**: the archive can only grow.

### Compliance-archive lifecycle (APPROVED MODEL; the destructive step is NOT implemented)

The customer owns its compliance records; NCollection is the processor and is never their owner. Offboarding must never destroy a record
that is subject to mandatory retention, and must not complete its destructive part until the customer (or a location the customer
explicitly designated) has the archive. The lifecycle of a tenant's compliance archive is:

`Active` → `Offboarding Requested` → `Compliance Archive Prepared` → `Archive Handoff Confirmed` → `Operational Offboarding` → `Retained Compliance Archive`

| Step | Who / what moves it | Entry condition | What it does | What it can never do |
|---|---|---|---|---|
| **Active** | — | the tenant exists | normal operation; the archive only grows | — |
| **Offboarding Requested** | an authorised customer contact, recorded by a platform administrator | a written request exists; the platform administrator is on the tenant's allow-list | freezes new e-invoice *preparation* (not reading, not export) and opens the tenant's offboarding record | delete anything |
| **Compliance Archive Prepared** | the tenant-side export service (to be built), run as the service identity | the previous step; no open transition in flight | builds the **archive package** for that tenant only: every XML ever archived (including superseded files), validation results, status/submission history, the retention rules and holds, a manifest with the sha256 of every file and of the whole package, the release and rule-set fingerprint each result was produced with | include another tenant's data; include any credential; modify a source record |
| **Archive Handoff Confirmed** | the customer's authorised contact, recorded with who, when, the destination and the package checksum the customer received (or the designated archive location and the checksum written there) | the package exists and its checksum was re-verified at the destination | the *only* gate that unlocks the destructive step | be confirmed by NCollection on the customer's behalf; be confirmed without the checksum match |
| **Operational Offboarding** | a platform administrator, only now | a confirmed handoff exists *and* `_nc_archive_status()` shows no open legal hold | operational data (users, documents, configuration) may be removed under the approved deletion policy; credentials are destroyed | remove the compliance archive unless the customer's confirmed destination now holds it and the policy says so; **if the handoff is not confirmed, destruction is REFUSED** with the reason |
| **Retained Compliance Archive** | — | operational offboarding done | the archive lives on at the customer's (or its designated) location, producible to the tenant and to the authority | be exposed to another tenant; be altered |

Rules that apply throughout:

* **No legal retention period is hard-coded.** How long a record must be kept comes only from a *confirmed* `ncollection.einvoice.retention.rule`
  row (with its citation) and the legal holds; until a rule is confirmed nothing is purgeable (`retain_until` is empty). The primary text is
  still to be read (OCQ-12).
* **Tenant isolation throughout.** The package is built inside the tenant's own database by the tenant-side service; it is stored and
  retrieved per tenant; the platform may read only counts (`_nc_archive_status()`: no content, no identifiers, restricted to the caller's
  companies). No export, download, API or administrative path accepts another tenant's identifier, and none exists that lists archives
  across tenants.
* **Custody by NCollection** only by explicit written agreement, then as an encrypted, tenant-segregated archive producible on the
  customer's or the authority's request.
* **Deletion requests** for regulated records are refused until `retain_until` has passed under a confirmed rule and no hold is open; erasure
  of personal data that conflicts with mandatory retention is a legal decision, not an engine default.

**What exists today:** the fail-closed retention rules and holds, the immutable and never-deleted archive, `_nc_archive_status()`, and
the tenant isolation. **What does not exist yet, deliberately:** the offboarding record and its states on the platform, the package
builder, the handoff confirmation, and any destructive step. No destructive deletion will be implemented until the platform-side
authorisation (who may request, who may confirm, which administrators) is specified and approved; the existing tenant deactivation and
deletion flows are untouched and still do not consult the archive status (a recorded gap, not an oversight).

## 11. Eligibility and activation (provisioning)

`ncollection_subscription/models/gated_modules.py` adds the *gated module* concept on top of the existing machinery; there is no second
provisioning system. Eligible = the tenant's country is the UAE **and** its books are verified as UAE books (`localization_applied_state
= done` for the package's chart/currency, produced by the existing tenant-side check — the registry's country label is not enough) **and**
the tenant already has Accounting. Activated = an explicit per-tenant `enable` override (`ncollection.tenant.module.override`, unique per
tenant+module, queued/deduplicated push) **with a recorded reason** (a ticket or approval reference, enforced where the decision is
stored). A plan that names the module is ignored; the picker never offers it; losing eligibility revokes the licence without deleting
the override or any data (and a tenant whose books were verified and are later refused is pushed immediately, not at the next reconcile); there is no bulk action and no country-wide switch. Not a companion
(companions follow their host automatically and refuse a per-tenant switch).

**The ASP adapter is gated too (Cycle 5).** `ncollection_uae_einvoicing_acube` is a second gated module whose `requires` is the engine:
it is eligible only while `ncollection_uae_einvoicing` is entitled AND itself eligible (the eligibility check now recurses for a gated
requirement instead of discarding gated modules), and it still needs its OWN `enable` override. So it can never reach a tenant without
the engine, never comes with a plan, and is optional per tenant. The core stays provider-neutral (it names no adapter). Tests:
`ncollection_saas` `test_a_gated_module_that_requires_a_gated_module_follows_it`, `test_the_real_registry_gates_the_acube_adapter_behind_the_engine`.
The validator workers and the tenant runner are infrastructure, not tenant entitlements: they serve only databases that hold the
licensed engine (the runner's eligibility probe), and the engine mirrors the licence at the ORM (`_nc_licensed`), so an unlicensed tenant
cannot use them even if a module were present.

**Tenant inventory (read-only, 2026-10-22)** — eligible with verified books: `uaetest`, `test11`; AE-registered but books **not** yet
verified by the check (`localization_applied_state=none`; running the existing, harmless "Apply localization" refreshes it): `atlas`,
`atlaseg`, `test12`, `test13`, `test14`, `awtar`; **refused / not eligible:** `wasla` (registered AE, books US/USD, localization
refused). **Pilot: `uaetest`.** Nothing has been enabled or changed on any tenant.

## 12. Tests and gates

**Final completion pass (2026-10-29), fresh database, final code:** full suite 594 / 594, 0 failed, 0 errors, 0 skipped (e-invoicing
502, A-Cube 41, localization 51); A-Cube 41 + gated eligibility 22 + Arabic coverage / PO format 5 = 68 / 68; official rules 105
expectations, 24 / 24 real-move documents (23 valid, 1 deliberately invalid refused by `ibr-193-ae`), worker 29 / 29; flake8,
invariants (+ tests), role matrix (+ tests), doc counts (+ tests), skip gate tests clean. **Architecture guard RUN on the uncommitted
work** with its own `--files` mode (no commit): clean, 104 files. Change in this pass: credential rotation is now attributed
(`rotated_by` / `rotated_at` on each credential, shown on the provider form, Arabic labels). Production `/queue_job/` block verified
compatible with the platform runner: `provisioning-runner` sets no `ODOO_QUEUE_JOB_HOST`, so OCA's jobrunner calls `runjob` on its own
container (`localhost:8069`), never through nginx.

**Cycle 5 (2026-10-29), fresh database:** full suite 594 tests, 0 failed, 0 errors, 0 skipped (e-invoicing 502, A-Cube adapter 41,
localization 51). Separate run on the final files: A-Cube 41 + gated eligibility 22 (`ncollection_saas`) + Arabic coverage and PO
format 5 (`ncollection_branding`) = 68 / 68. Official rules: 105 expectations hold (33 official samples, 20 engine-built documents, 9
negative controls, 24 / 24 real-move documents: 23 valid, 1 deliberately invalid refused by `ibr-193-ae`), worker 29 / 29. No A-Cube
sandbox test exists: every A-Cube test runs against a scripted server built from the published contract.

**Cycle 4 (2026-10-29), fresh database, final code:** 588 tests, 0 failed, 0 errors, no skips (e-invoicing 502, A-Cube adapter 35,
UAE localization 51); 6 min 22 s wall. New: key rotation 12, evidence export 12, foreign buyer 5, runner resilience 3, received
self-billed credit note + never-posted 2, A-Cube 35. Official rules: `e2e_check.py` 105 expectations hold, 24 / 24 real-move XMLs as
expected (the new `real-foreign-buyer-380` included), worker 29 / 29. Arabic: the repository's coverage guard
(`ncollection_branding` `TestTranslationCoverage`) passes for all three modules; its platform-module check needs `ncollection_saas`
installed and is not meaningful on this scratch install. Inbound evidence: the received documents in `test_inbound` /
`test_self_billing` are the engine's own officially-validated XMLs, so no separate inbound XML exists to validate.

**Cycle 3 (2026-10-29), measured on a fresh database:** the module suite is **468 tests, 0 failed, 0 errors** (397 after Cycle 2).
New: self-billing 28 (`test_self_billing.py`: eligibility 13, documents 8, transport 4, received 3), credit notes 20
(`test_credit_notes.py`), scenario matrix 23 (`test_scenario_matrix.py`). With `NC_EINVOICE_EXPORT_DIR` the suite exports 23
real-move XMLs (8 before); `e2e_check.py` validates all 23 under the official rules (the Self-Billing ones on the Self-Billing
worker) and every other expectation holds; worker unit tests 29/29. The paragraph below is the original Phase-1 baseline.

Engine 228 tests (pure logic 67 + review-found logic 9, validator client 23, security / isolation / secrecy / state / archive /
retention 37, hardening against forgery 37 + 13, mapper on real invoices 28, pinned resources 14), gate 30, master data (module suite) 51,
worker 29 (stdlib), real-rule end-to-end `e2e_check.py` (runs in the worker image: 30 official samples, 13 engine-built document types
including the exempt allowance, negative controls, token / signature / forged-answer controls). A pre-existing failure on `develop` —
`ncollection_saas` `TestApprovalGate.test_an_undecided_request_expires_and_releases_its_subdomain` (unique-name collision) — fails
identically on the pristine tree and is unrelated.

## 13. Open compliance questions

OCQ-1 TIN origin (still open between Mandatory Fields V1.1 and Guidelines V2.0, §4) · OCQ-2 buyer business acceptance message · OCQ-3
MLS vocabulary and negative-MLS duties (which MLS version the UAE PASR requires is not confirmed) · OCQ-4 precision of a derived net
price · OCQ-5 HS/SAC minimum digits · OCQ-6 metal-scrap RCM goods type: listed by Guidelines V2.0 §10.5.1, absent from the PINT-AE
1.0.4 GoodsType list and refused by fatal `ibr-006-ae` · OCQ-7 tourist-refund tax category · OCQ-8 exchange-rate source · OCQ-9
rounding on multi-rate/foreign/allowance documents (measure per pilot) · OCQ-11 self-billing codes: **answered** (PINT AE Self-Billing
1.0.4 lists 389/480 and 261/81) · OCQ-12 retention primary text (Cabinet Decision 74/2023 Art 3, cited by Guidelines V2.0 §5.4) ·
OCQ-13 Gazette status of MD 197 / MR 66 (both PDFs carry blank publication dates) · OCQ-14 UUID authority (Guidelines V2.0 §5.2 #7:
the ASP) · OCQ-15 which PINT-AE release is required from 1 Jan 2027 · OCQ-16 Arabic names in the XML · OCQ-18 same-day VAT point
date (the engine omits BT-007 unless strictly earlier, per `ibr-141-ae`) · **OCQ-19** Cabinet Decision 173/2026 (penalties) is cited
by Guidelines V2.0 §11.2 but not published; only CD 106/2025 is cited · **OCQ-20** exempt-only documents: fatal `ibr-151-ae` vs
Guidelines V2.0 App 6 §18.3.1 (decision recorded in §3) · **OCQ-21** the start of the 24-month airline air-waybill exclusion (MD 243
Art 4(1)(d): "the date on which the Electronic Invoicing System becomes effective") is not tied to one calendar date for every
Person · **OCQ-22** the Tax Group exclusion is an Excluded Transaction in MD 197 Art 2 but a timing-only grace period in Guidelines
V2.0 §6.3.2.1; the engine applies the Decision text and cites both · **OCQ-23** the VAT-Law issuance timeline for Registrants (MD 243
Art 6(4)) has not been read from the VAT Decree-Law itself; the engine models it as a separate, unconfirmed rule.

## 14. Arabic UI status

`ncollection_uae_einvoicing/i18n/ar.po` (and the additions to `ncollection_account_localization_uae` and `ncollection_subscription`) are an
**engineering draft**, generated and bound to their exact source references so the repository's translation-coverage guard passes. The
Arabic UI is **not** claimed complete. Before release a native Arabic reviewer must confirm the compliance terminology, especially:
*E-Invoice*, *TIN*, *TRN*, *Participant ID*, *ASP*, *Validation*, *Readiness*, *Submission*, *Accepted*, *Rejected*, *Validation Error*,
*Compliance Archive*. Established NCollection terms were reused where they exist (the merge takes existing catalogue entries first) and no
existing translation was changed. Messages that the engine builds at run time from its library (readiness and reconciliation findings)
are stored on the record in the language of the user who ran the action; they are not yet in the catalogue.

## 14a. Requirement status (Cycle 3 review, 2026-10-29)

The R-numbers come from the 2026-10-28 compliance audit, whose two drafts numbered some rows differently; where a number named two
requirements, both are reviewed. Status only with evidence; "ASP phase" = the remaining part needs a real ASP adapter (Cycle 4).

| Req | Requirement | Status | Code | Tests | Evidence / what remains |
|---|---|---|---|---|---|
| R05 | Buyer identifier IBT-046 / IBT-047; foreign buyer (G2.0 §18.2.2-18.2.3; `ibr-135/136/149-ae`, `ibr-cl-10`) | COMPLIANT | localization `nc_foreign_tax_id/_scheme`; `identity.buyer_endpoint`; `prepare._nc_party`; `xml_builder._party`; readiness | test_foreign_buyer (5), `real-foreign-buyer-380` (official) | IBT-048 for a foreign VAT id is not populated (G2.0: only if applicable; no field modelled) |
| R20 | Self-billed Tax Invoice 389 / Tax Credit Note 261 (G2.0 §10.3) | COMPLIANT (engine) | §9d; `spec`, `self_billing.py`, `prepare`, `readiness` | test_self_billing (28), e2e SB section, real-move SB fixtures | official SB rules pass for 389 / 261; submission to a real ASP: ASP phase |
| R20′ | No cancellation after submission (MD 243 Art 6(2)(a)) | COMPLIANT | `account_move.py` | test_security_archive | unchanged, re-run green |
| R25 | Free zone: beneficiary (`ibr-007-ae`) | COMPLIANT | prepare / xml_builder | `test_free_zone` (+ official), negative | engine-built FTZ document validated by the official rules (it was "official sample only") |
| R25′ | Margin scheme: N, VAT 0 (`ibr-116/108-ae`) | COMPLIANT | prepare (`N` shown rate), tax-map guard | `test_margin_scheme` (+ official), 3 negatives | was blocked on a real move (defect fixed, §6a) |
| R30 | Export: endpoint 99, deliver-to (`ibr-152-ae`) | COMPLIANT | identity, prepare (`cac:Delivery`) | crosscheck export (+ official), negative | retested in the matrix |
| R30′ | Disclosed agent: principal (`ibr-137/176/177-ae`) | COMPLIANT | readiness `_agent` | `test_disclosed_agent_billing` (+ official), 2 negatives | the principal stays an ID (what BTAE-14 is); no principal partner needed by the rules |
| R40 | Outbound submission (queue, retry, idempotency, timeout) | COMPLIANT (engine) | §9a | test_transport (25), test_self_billing transport (4) | against the contract adapter; a real ASP: ASP phase |
| R40′ / R41 | Confirmations C3→C1 / C5→C1 (MLS, TDD reported) | COMPLIANT (engine) | `ncollection.einvoice.event`, `mls_state`, `fta_state` | test_transport, `test_mls_and_tdd_confirmations_are_stored_not_generated` | stored from the ASP; vocabulary of a real ASP: ASP phase (OCQ-3) |
| R41′ | TDD generation | NOT APPLICABLE | — | — | the ASP's act (G2.0 §5.1); never generated here (decision 2026-10-28) |
| R43 | Inbound receiving into accounting (MD 243 Art 6(3)) | COMPLIANT (engine) | §9c | test_inbound (31), received self-billed (3) | DRAFT documents only; a real ASP's inbound API: ASP phase |
| R43′ | UUID authority (G2.0 §5.2 #7; `ibr-193-ae`) | COMPLIANT (engine) / ASP-DEPENDENT | `einvoice.uuid` (ours, locked) vs `asp_reference` | real-move negative control, test_security_archive | whether A-Cube keeps `cbc:UUID`: SANDBOX-VERIFY (§9f) |
| R46 | No system-failure notice to the FTA (MD 197 Art 3 repeals Art 12) | COMPLIANT | — (deliberately absent) | — | nothing builds one; must stay absent |
| R46′ | Retention integrity / export package (MD 243 Art 11; G2.0 §5.4) | COMPLIANT (export) | `models/export.py`, `lib/export_package.py` | test_export_package (12) | the destructive purge stays unbuilt by decision (§10) |
| R48 | Secrets at rest, rotation, credential screen | COMPLIANT | `secret.py`, `secret_crypto.keyring_from_env`, wizard | test_key_rotation (12), test_security_archive | — |
| R48′ / R49′ | Callback authentication, replay protection, idempotency | COMPLIANT (engine) | `controllers/callback.py`, adapter `verify_callback`, `UNIQUE(provider, message_id)` | test_transport (forged / replayed / duplicate / reordered) | each adapter implements its provider's signature: ASP phase |
| R49 | Immutability and tamper evidence | COMPLIANT | `prepare` (`xml_generated` / `xml_superseded` carry the sha256 + attachment), `ir_attachment` guard, locked fields | test_security_archive, test_prepare | the 2026-10-28 gap ("no digest per archived XML in the history") was already closed: every archived XML's sha256 is in its history row |
| Endpoints | Seller `0235:<TIN>`; buyer endpoint; predefined 98 / 97 / 99; self-billing roles | COMPLIANT | `identity.py`, `prepare._nc_parties` | test_pint_logic, matrix (deemed 97), crosscheck (export 99), SB (`seller_endpoint 0235:<supplier TIN>`) | the Peppol participant LOOKUP (SML/SMP) is the ASP's (G2.0 §5.2 #6): NOT APPLICABLE to Corner 1 |

## 15. Pre-merge review (2026-10-22) and what remains

Four independent read-only reviews (code, Odoo conventions, tenant isolation, security) were run against this work before the first
commit. Every HIGH finding was fixed and pinned by a test: the forgeable context flag and unguarded `create`; the forgeable history and
findings; the world-readable archive file (`public`); `company_id` not tied to the document; the unauthenticated, unsigned worker and its
fail-open report parsing; reconciliation crashing on a mixed tax-less / taxed document; an exempt document allowance missing
`Percent 0`; a volume-discount credit note that referenced its invoice; the dead end after a generated XML; and the vendored official
files not protected from line-ending conversion. MEDIUM findings fixed: ACLs, secret identity, retention-rule and archive-status
scoping, hold finality, licence mirrored at the ORM, the company switch honoured, payload language, GTIN, cash rounding, company
currency, the reason required at the override, an immediate push when verified books are later refused, the audit seeder excluding the
regulated models.

A second review of the fixes themselves then found and closed: the `default_*` context bypass of the create whitelists (e-invoice,
archive attachment, provider, hold), the bearer and the signing key being the same secret and a replayable answer (now derived keys and
a nonce), an archive file that could be tokenised by the engine, the data a user types staying editable after checking, the archive
status forgetting a superseded record, a hold closed before it was opened, an unhandled exception on odd JSON, and a UTF-16 document
slipping past the DOCTYPE scan — each pinned by a test.

**Recorded, not done (non-blocking follow-ups):**

* The validator call is **synchronous** inside the user's request (bounded by a 20 s timeout and fail-closed). The design note
  `DESIGN_CRON_AND_QUEUE_TOPOLOGY.md` prefers a queue for work that can block on another server; adopting `queue_job` here is a decision
  deferred to the submission phase, where an ASP call makes it necessary anyway.
* `_nc_map_document` (about 240 lines) and `xml_builder.build` are longer than the repository's size guideline and `tests/test_pint_logic.py`
  exceeds 800 lines; splitting them is a mechanical refactor with no behaviour change, deferred to keep this diff reviewable.
* The buyer's address is read from the commercial partner, not the invoice contact.
* The submission phase must re-run the payload comparison as a precondition of `queued` (the lock check already stops accounting edits
  after submission).
* No key rotation, no credential screen, no UI for the engine (backend only), no provider adapter, and the service account must be
  created as a non-login user by whoever deploys it.
* Eligibility rests on the existing localization check's recorded message; a dedicated timestamped fact is a possible hardening.
* `nc_trn` / `nc_tin` / the legal registration are editable by anyone who can edit a partner (tracked in the chatter); a restricted group
  is a possible hardening.
* Saxonica's notices must be collected before the worker image is distributed outside NCollection.
* An Odoo `_get_raw_access_token` token can still be minted by a user who can already read the archive file, which is their own act of
  sharing; it grants no more than their own access.
