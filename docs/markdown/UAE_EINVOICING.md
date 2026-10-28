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
* **No ASP is selected or built in.** The core knows only an adapter interface; a future `ncollection_uae_einvoicing_<asp>` module is
  the only place transport and provider credentials' *use* will live. There is no HTTP client in the core and no fake endpoint.
* **Scope:** customer invoices (`out_invoice`) and customer credit notes (`out_refund`). A vendor bill is a *received* document and is
  never an outbound e-invoice (creating one is refused). Self-billing and inbound e-invoices are future, separate flows.
* The **PDF is unchanged**: it is the printable representation. The e-invoice is the structured XML. No QR code or barcode is added
  (the UAE e-invoice has none). [FACT: Guidelines V2.0 §5.3]

## 2. Pinned official resources

`resources/pint_ae/1.0.4/` holds **unmodified** copies of: the two Schematron bundles (`.sch` and the preprocessed `.xslt`), 17 code
lists, 27 invoice and 3 credit-note samples. `MANIFEST.txt` gives the sha256 of every file and of the source archive
(`1e8b0bd5…491f`, 1,019,347 bytes, `Last-Modified` 2026-07-29, release 1.0.4 of 2026-06-02, status Final). `lib/spec.py` is the only
place the release, the specification identifiers and the constants live; `verify_resources()` detects a changed, missing or
unlisted file and a test fails if the version string appears anywhere else in `lib/`. Adding a release is a new profile and a new
folder, not a code search (the Schematron was revised six times in its first year).

## 3. Upstream artifact discrepancies (recorded, never "fixed")

Registered in `lib/upstream.py`, each with evidence, the engine's handling and a check that it is *still true* of the pinned files, so a
new release that corrects one forces a human review.

| Id | Finding | Handling |
|---|---|---|
| `greek-nu-margin-code` | the code-list file gives the margin-scheme category as GREEK capital Nu (U+039D); the Schematron and the official sample use LATIN `N` | the engine emits Latin `N`; the file is read as published; `tax_categories` is an explicit tested set |
| `hs-sac-min-digits-placeholder` | rule `ibr-187-ae` (minimum digits of HS/SAC) is a placeholder: `test="true()"`, message says "X digits" | digits-only is an NCollection *warning*; no length is enforced (OCQ-5) |
| `self-billing-type-codes-missing` | the BILLING type lists contain 380/480 and 381/81 only; the self-billing codes are in the separate PINT AE Self-Billing 1.0.4 lists (389/480, 261/81) | self-billing out of scope this phase; no such code is ever emitted (OCQ-11 answered by the Self-Billing lists) |
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
* **UUID (OCQ-14):** the Guidelines say the ASP generates it, yet rule `ibr-193-ae` requires it in the document. The engine stores a
  `uuid` set once and never reused as another identifier; the ASP-side authority is left to the adapter.

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
check. Unset master key ⇒ the feature refuses; it never falls back to plaintext. **Not built yet:** key rotation (`key_version` is
always 1) and a screen; the service account must be created as a non-login user (nothing enforces that yet).

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

## 10. Retention and tenant offboarding

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

**Tenant inventory (read-only, 2026-10-22)** — eligible with verified books: `uaetest`, `test11`; AE-registered but books **not** yet
verified by the check (`localization_applied_state=none`; running the existing, harmless "Apply localization" refreshes it): `atlas`,
`atlaseg`, `test12`, `test13`, `test14`, `awtar`; **refused / not eligible:** `wasla` (registered AE, books US/USD, localization
refused). **Pilot: `uaetest`.** Nothing has been enabled or changed on any tenant.

## 12. Tests and gates

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
