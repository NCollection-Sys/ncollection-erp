# A-Cube sandbox execution pack (UAE e-invoicing)

**This is an execution checklist. NOTHING in it has been executed against A-Cube.** Real-sandbox evidence today: **none**. Every A-Cube
test in the repository runs against a scripted server built from A-Cube's published contract (OpenAPI
`docs.acubeapi.com/openapi/gov-ae-api.json`, sha256 `858d1060…ddff`, fetched 2026-10-29). Background: `UAE_EINVOICING.md` §9f
(adapter, audit, identifiers, status mapping) and §9g (deployment).

Step classes, never mixed: **LOCAL-PREPARATION** (done or doable without A-Cube), **ASP-DEPENDENT** (needs the live sandbox),
**DEPLOYMENT-PENDING** (a production act; out of scope until release), **HARD-STOP** (an ASP-dependent step whose failure stops the
campaign and reopens the design).

---

## 1. Inputs that must exist before step S01 (the only external blocker)

| Input | Source | Stored where | Never |
|---|---|---|---|
| A-Cube account **email** + **password** for `environment: sandbox` | A-Cube (account registration) | provider form > *Set a credential* (`email`, `password`) — encrypted at rest | in git, evidence, logs, chat |
| Sandbox **legal entity A** (onboarded via A-Cube's EmaraTax flow) with a test TIN | A-Cube | company master data (TIN, TRN) | a real taxpayer's identifiers |
| Sandbox **legal entity B** (genuinely distinct: other TIN / TRN / legal registration) | A-Cube | a second scratch tenant (or second company) | entity A's identifiers reused |
| Webhook token: 32+ random characters (`openssl rand -hex 32`) | generated locally | credential `webhook_token` + the A-Cube event destination | in evidence |
| A public HTTPS URL that forwards to the dev Odoo callback route | a tunnel the operator chooses | A-Cube event destination `target` | the production edge |

**Missing tooling (documented, not invented):** the repository has no tunnel command and no evidence-collection script. Both steps
below are manual; the commands shown are the ones that exist.

## 2. Environment commands (existing ones only)

| Purpose | Command (exists) |
|---|---|
| Diagnose the dev environment | `make doctor` |
| OCA addons present (fresh worktree) | `make oca` |
| Dev stack up / down | `make up` / `make down` |
| Validator workers (Billing + Self-Billing; needs `NC_EINVOICE_VALIDATOR_TOKEN` in `.env`) | `make einvoice-validator-up` / `make einvoice-validator-down` |
| Tenant e-invoice runner (dev overlay) | `docker compose -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.einvoice-runner.yml up -d einvoice-runner` (header of that file) |
| Stop the runner | the same files, `rm -sf einvoice-runner` |
| Install the modules in the scratch tenant DB | `make install m=ncollection_uae_einvoicing,ncollection_uae_einvoicing_acube db=<scratch tenant>` |
| Odoo shell (inspection) | `make shell` |
| Official rules on exported XML | `make einvoice-e2e EINVOICE_FIXTURES=<dir>` |
| Stack sanity before trusting a scary result | `scripts/dev/stack_settled.sh` |

Environment variables (in `.env`, gitignored): `NC_EINVOICE_SECRET_KEY` (≥ 32), `NC_EINVOICE_SECRET_KEY_VERSION` (1),
`NC_EINVOICE_VALIDATOR_TOKEN` (≥ 32). `NC_EINVOICE_VALIDATOR_URL` / `_SB_URL` are set by the validator overlay.

## 3. Test data (reuse the canonical fixtures; replace identifiers with A-Cube-issued test ones)

The SHAPES come from `tests/common.py`, `tests/test_self_billing.py`, `tests/test_scenario_matrix.py`, `tests/test_foreign_buyer.py`;
the IDENTIFIERS for the sandbox are the ones A-Cube issues for entities A and B (do not reuse fixture TINs on a real network).

| Role | Entity | Required master data | Fixture shape |
|---|---|---|---|
| Seller (standard billing) | A | legal name, TRN 15 digits, TIN 10 digits, legal registration TL + authority, address with emirate, IBAN | `EInvoiceCase._company` |
| Standard buyer | B | same as seller | `EInvoiceCase.buyer` |
| Foreign buyer | partner of A (country ≠ AE) | foreign tax id + ICD scheme (e.g. 0088), address abroad | `TestForeignBuyer.foreigner` |
| Self-billing supplier | B, as partner of A | valid TRN, IBAN; `issue` agreement on A | `SelfBillingCase.supplier` |
| Self-billing customer (received) | A, as partner of B | `receive` agreement on B | `TestReceivedSelfBilled` |
| Inbound supplier | B sending to A | matched by TRN / TIN / legal id | `InboundCase.supplier` |

Tax map on A: sale S 5 %, Z 0 %, E 0 % with exemption code, O 0 %, AE 0 % with goods type + shown rate 5, N 0 % with shown rate 5;
purchase S 5 % (inbound, self-billing). Products: one goods item with HS code, one service with SAC code.

| Document | Type | Lines (deterministic) | Notes |
|---|---|---|---|
| Tax invoice | 380 | 2 × 100.00 S 5 % | `_invoice()` default |
| Tax credit note | 381 | reverse of the 380, reason `DL8.61.1.E` | full credit |
| Commercial invoice | 480 | 1 × 100.00 O | out of scope only |
| Commercial credit note | 81 | 1 × 40.00 E against a 480 | |
| Self-billed tax invoice | 389 | vendor bill 2 × 100.00 purchase S 5 % | needs active `issue` agreement |
| Self-billed tax credit note | 261 | reversal of the 389, reason `DL8.61.1.E` | |

## 4. Evidence directory (specification only; created when the campaign starts)

```
sandbox-evidence/<YYYYMMDD>-<run id>/
  manifest.json                  the index below; the ONLY file that lists results
  00-environment/  01-auth/  02-outbound/  03-status/  04-uuid/  05-webhooks/  06-mls/  07-tdd/  08-inbound/
  09-self-billing/  10-credit-notes/  11-scenarios/  12-all-document-types/  13-final-state/
```

**Never inside the repository** (keep it beside the run, outside git). File names: `<step id>__<what>.<ext>`, lower-case,
`[a-z0-9._-]` only (e.g. `S04__source-xml-uuid.json`). Every file is listed in the manifest with its sha256; a file not listed is
not evidence. Retention: the run folder plus the final evidence-export package (S24) are kept with the release record; nothing is
edited after capture (a correction is a new file).

### manifest.json (schema)

```json
{
  "run_id": "string", "started_at": "ISO-8601", "operator": "string", "environment": "sandbox",
  "code_ref": "the working-tree state, e.g. 'uncommitted on feature/uae-einvoicing-foundation-sprint'",
  "entries": [{
    "step_id": "S01..S24",
    "timestamp": "ISO-8601",
    "tenant_db": "string", "company": "string",
    "document_type": "380|480|381|81|389|261|null", "document_number": "string|null",
    "our_uuid": "string|null",
    "provider_document_id": "string|null      (A-Cube invoiceId, as stored: '<collection>:<uuid>')",
    "provider_event_id": "string|null         (A-Cube eventId when the webhook carries one; else null)",
    "provider_status": "string|null           (exactly as returned; NEVER normalised)",
    "http_status": "integer|null",
    "correlation_id": "string|null", "idempotency_key": "string|null",
    "evidence": [{"file": "relative path", "sha256": "hex"}],
    "result": "PASS|FAIL|NOT TESTABLE IN SANDBOX|BLOCKED",
    "asp_dependent": true,
    "notes": "observations; open questions (section 8)"
  }]
}
```

Fields A-Cube has not documented are not in the schema (no MLS id, no provider timestamp: A-Cube's published status resource has
none); if the sandbox shows one, record it under `notes` and amend the schema afterwards.

## 5. Capture and redaction rules

| Capture | How (dev only) | Keep | Redact / never |
|---|---|---|---|
| Odoo e-invoice | `make shell` → read `uuid, local_state, submission_state, exchange_state, fta_state, mls_state, asp_reference, fta_reference` | JSON of those fields | — |
| Submission + attempts | `ncollection.einvoice.submission` (+ `attempt_ids`) | idempotency key, correlation id, state, attempt number, http status, error code/message, retry time | — |
| A-Cube response | the stored digest (`response_sha256`) and the error code / path the adapter kept | digest + code | response bodies are NOT stored by the engine; do not add them |
| Webhook | `ncollection.einvoice.event` rows (`source=callback`, `message_id`, `kind`, `status`, `metadata_json`, `create_date`) | those fields | the token header value |
| Provider status | the event `metadata_json.acube_status` | as returned | — |
| Inbound document | intake row + its archived XML sha256 | metadata; the XML only where the step's result depends on its content (S18) | personal data beyond the parties' business identifiers |
| MLS | whatever A-Cube returns (documents of type `mls`) | ids, type, the reference it carries | — |
| TDD | the file via the adapter / API, its sha256, the `fta_reference` | file + sha256 | — |
| Validator result | the e-invoice findings (`validation_ids`) and the run line (engine, release, rules fingerprint) | as stored | — |

**Never captured:** passwords, the JWT, the webhook token, `NC_EINVOICE_SECRET_KEY*`, private keys, `Authorization` headers, full HTTP
request dumps. **XML:** keep the full XML only for S04 (the UUID proof), S18 (inbound content), S20 (one per document type) and any
failing step; elsewhere the sha256 + the e-invoice id is sufficient (the engine's archive keeps every XML anyway, S24 exports it).

## 6. Steps

| ID | Class | Action | Odoo state to inspect | A-Cube evidence | Retain | Pass | Fail means |
|---|---|---|---|---|---|---|---|
| S01 | ASP-DEPENDENT | credentials set; *Refresh status* on any submitted doc (first call logs in) | no error; no credential in the Odoo log | JWT obtained (do NOT store it) | `01-auth/S01__log-excerpt.txt` (grep shows no secret) | call succeeds, one login | 401: account / environment |
| S02 | ASP-DEPENDENT | entity A: 380 → Prepare → Generate XML → Validate → *Submit to ASP*; runner executes the job | `local_state=validated`, submission `done`, `submission_state=asp_accepted` | `POST /invoices/outgoing` 201 | `02-outbound/S02__submission.json` | asp_accepted | 404 legal entity; 422 → code + path |
| S03 | ASP-DEPENDENT | *Refresh status* | new `exchange` event; `metadata_json.acube_status` | `GET /invoices/{id}` | `03-status/S03__events.json` | an event with the RAW status | malformed / no status = adapter defect |
| **S04** | **HARD-STOP** | read `/source` of S02's document (`make shell`: `adapter._call('GET','/invoices/<id>/source', accept='application/xml')`) and compare `cbc:UUID` with `einvoice.uuid` | `uuid` | the stored XML | `04-uuid/S04__source-xml.xml`, `S04__compare.json` | identical | **STOP: the duplicate proof (§9f.2) must be redesigned before anything else** |
| S05 | ASP-DEPENDENT | compare `asp_reference` with A-Cube `invoiceId` | `asp_reference` | id | `04-uuid/S05__ids.json` | `invoices:<invoiceId>` | adapter defect |
| S06 | ASP-DEPENDENT | idempotency: run the same submission job again; then force a lost answer (drop the tunnel / network after POST) and let it retry | ONE submission, same key / correlation id, retry `duplicate=True` | ONE document in `GET /invoices?direction=outgoing&tin=<A>` | `02-outbound/S06__list-before-after.json` | count unchanged | a second document = release blocker |
| S07 | ASP-DEPENDENT | observe the status of an accepted document over time | events as received | statuses | `03-status/S07__timeline.json` | the real meaning of `accepted` recorded | — |
| S08 | ASP-DEPENDENT | provoke a rejection (ask A-Cube how; the engine validates first, so the official rules cannot be broken from the UI) | `asp_rejected`, `error_code`, path | 400 / 422 | `03-status/S08__rejection.json` | rejection classified REJECTED | wrong class = mapping defect |
| S09 | ASP-DEPENDENT | `action_required` if ever produced | stored, unmapped event | status | `03-status/S09__*.json` | recorded as observed | NOT TESTABLE IN SANDBOX if absent |
| S10 | ASP-DEPENDENT | `sending_failed` if produced | as S09 | status | `03-status/S10__*.json` | as S09 | as S09 |
| S11 | ASP-DEPENDENT | `withdrawn` if offered | as S09 | status | `03-status/S11__*.json` | as S09 | as S09 |
| S12 | ASP-DEPENDENT | register the event destination (`authType=header`, `authKey=X-NC-Webhook-Token`); new 380; replay one webhook (resend from A-Cube or curl with the same body) | events with `source=callback`; the replay adds no applied event; no state from the body | `document.delivered` | `05-webhooks/S12__events.json` | state changes only via re-read; replay ignored | 401: header name/value; state from body = defect |
| S13 | ASP-DEPENDENT | find MLS documents (`GET /invoices?direction=incoming`, `documentType mls`) and how they reference S02 | `mls_state` (moves only when mapped) | MLS resource(s) | `06-mls/S13__mls.json` | relationship documented | define mapping before release |
| S14 | ASP-DEPENDENT | *Refresh status* after A-Cube reports the TDD | `fta_state=reported`, `fta_reference=acube-tdd-sha256:<sha>` | `GET /invoices/{id}/tdd` 200 | `07-tdd/S14__tdd.xml`, sha256 | sha256 equals `fta_reference` | 204 / 404: not yet; repeat |
| S15 | ASP-DEPENDENT | list outgoing / incoming | — | real JSON shape | `13-final-state/S15__list-shape.json` | array or `hydra:member` (both handled) | other shape → `_items` change |
| S16 | ASP-DEPENDENT | paging with > 100 documents (or ask A-Cube for `itemsPerPage` behaviour) | cursor advances | pages | `13-final-state/S16__pages.json` | complete listing | — |
| S17 | ASP-DEPENDENT | ask / try a number filter | — | answer | `13-final-state/S17__filter.txt` | documented | — |
| S18 | ASP-DEPENDENT | entity B sends a 380 to A | `inbound` event, intake row, XML archived with sha256 | `document.received`, `/source` | `08-inbound/S18__intake.json`, XML | intake `matching`/`ready` | `rejected` wrong recipient: master data |
| S19 | ASP-DEPENDENT | runner processes S18; repeat delivery / poll | DRAFT vendor bill, `nc_inbound_id` set; a repeat = no second record | — | `08-inbound/S19__bill.json` | draft only, one bill | posted / two bills = blocker |
| S20 | ASP-DEPENDENT | the six types (section 3) from A | each `asp_accepted` | per type | `12-all-document-types/S20__<type>.json` + XML | all accepted | 4xx: compare with official result |
| S21 | ASP-DEPENDENT | the scenario matrix (section 7) | each validated + accepted | per case | `11-scenarios/S21__<case>.json` | as section 7 | record observed |
| S22 | ASP-DEPENDENT | self-billing: A issues 389 / 261 for B; B self-bills A | outbound accepted; at the receiver a DRAFT customer invoice / credit note | self-billed types | `09-self-billing/S22__*.json` | as expected | owner (TIN of the BUYER) mismatch |
| S23 | ASP-DEPENDENT | credit notes: full 381, partial 381, 81 for a 480 | accepted; `BillingReference` | — | `10-credit-notes/S23__*.json` | accepted | — |
| S24 | LOCAL-PREPARATION (after the run) | *Create Evidence Package* on A and B; `export_package.verify` | export record + package sha256 | — | `13-final-state/S24__export.json` | verify() returns no problem | any problem = defect |
| S25 | DEPLOYMENT-PENDING | everything in section 9 | — | — | release record | — | — |

## 7. Scenario cases for S21 (fixtures: `tests/test_scenario_matrix.py`, `test_real_move_crosscheck.py`, `test_foreign_buyer.py`)

| Case | Type / profile | Required data | Expected XML | Local validator (VERIFIED) | Live evidence (ASP-DEPENDENT) |
|---|---|---|---|---|---|
| Free zone | 380 Billing, `10000000` | beneficiary id | `BuyerCustomerParty/PartyIdentification` | `real-scenario-free-zone` passes | accepted |
| Deemed supply | 380, `01000000` | — | buyer endpoint `9900000097`, no PaymentMeans | `real-scenario-deemed-supply` passes | accepted; report-only behaviour |
| Margin scheme | 380, `00100000` | N tax 0 %, shown 5 | `TaxCategory N`, Percent 5, VAT 0 | `real-scenario-margin-scheme` passes | accepted |
| Summary | 380, `00010000` | period start / end | `InvoicePeriod` | `real-scenario-summary` passes | accepted |
| Continuous supply | 380, `00001000` | frequency + period | `InvoicePeriod/Description` | `real-scenario-continuous-supply` passes | accepted |
| Disclosed agent | 380, `00000100` | principal TRN ≠ seller TRN | `SellerSupplierParty` | `real-scenario-agent-billing` passes | accepted |
| E-commerce | 380, `00000010` | delivery address | `cac:Delivery` | `real-scenario-ecommerce` passes | accepted |
| Export | 380 Z, `00000001` | buyer abroad, delivery address abroad | endpoint `9900000099`, `cac:Delivery` | `real-export-scenario-380` passes | accepted; report-only behaviour |
| Foreign buyer | 380 | foreign tax id + ICD | endpoint `9900000098`, `PartyIdentification@schemeID` | `real-foreign-buyer-380` passes | accepted |
| Combinations | 380 | as above | `10000010`, `00011000` | both pass | accepted |

## 8. Questions for A-Cube (the published contract does not answer them; each maps to a step)

1. Is `cbc:UUID` returned unchanged by `/source`? (S04)
2. Meaning and finality of `accepted`, `rejected`, `withdrawn`, `action_required`, `sending_failed`, `processed`. (S07–S11)
3. How MLS / TDD-MLS documents reference the original outgoing document. (S13)
4. An idempotency key, or a list filter by document number? (S06, S17)
5. Webhook payload fields; any signed option beyond the static token? (S12)
6. Behaviour for predefined endpoints `9900000097/98/99`. (S21)
7. Test TINs / entities in the sandbox; two entities on one account? (inputs)
8. Rate limits and `Retry-After`. (S06)

An observed value that differs from the published contract is recorded verbatim, flagged in `notes`, and reopens the matching row of
`UAE_EINVOICING.md` §9f.3 / §14a; no mapping is added until the meaning is known.

## 9. Production preflight (inspection only — nothing executed)

| Item | State | Class |
|---|---|---|
| `/queue_job/` block in `nginx/conf.d/ncollection.prod.conf` (443) + `tenant-custom-domain.conf.j2` + rendered tenant confs; then `nginx -t`, reload, `curl -o /dev/null -w '%{http_code}' https://<host>/queue_job/runjob` = 403 | written (§9g), compatible with the platform runner (it calls its own container) | requires production change |
| Tenant runner service (one instance, limits, SIGTERM grace ≥ 600 s) | specified (§9g); dev overlay proven | requires production change + final PR/merge |
| Validator workers (Billing + Self-Billing), internal network, read-only | specified; dev overlay proven | requires production change + final PR/merge |
| Secret store: master key (+ version), validator token | variables specified (§8, §7) | requires production change |
| A-Cube production credentials + legal entity onboarding | none | requires external credential |
| Tenant gating: per-tenant enable of engine, then adapter | implemented + tested (§11) | ready (operator act at go-live) |
| Monitoring: submissions in `attention` / `dead`, inbound `review` / `unmatched` (menus exist); runner log lines (joined / left / could not be joined) | available in the UI and logs; no alerting wired | requires production change (alert rules) |
| Rollback | disable the gated overrides (licence revoked, data kept), stop the runner; nothing is uninstalled; submitted documents stay locked (legal) | ready (documented) |
| Architecture guard in CI on the final commit; Playwright | not run in CI (uncommitted) / environment blocker | requires final PR/merge |
