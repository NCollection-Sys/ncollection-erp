# Design Decision — Automated Exchange-Rate Freshness (#236)

**Date:** 2026-08-04 · **Author:** DEV-1 · **Status:** **accepted** (owner sign-off 2026-08-04) ·
**Scope:** how NCollection keeps tenant currency rates fresh. Follow-up to **P3-T06** (#46),
which shipped UAE multi-currency on **seeded fixed pegs** and deferred automated freshness.

This record decides *topology*, *provider*, and *build-vs-adopt*. It ships **no code** — the
implementation is a follow-up ticket, deliberately, because the security envelope (§5) did not
exist when the work was filed.

## Decisions

| # | Decision | Choice |
|---|---|---|
| D1 | Where the fetch runs | **Centralized** — admin DB fetches once, pushes to tenants over the existing config-sync channel (P2-T03) |
| D2 | Rate source | **ECB only.** Floating currencies refresh; AED + GCC pegs stay as #46's static rows |
| D3 | OCA `currency_rate_update` | **BUILD CUSTOM** — do not pin `OCA/currency` |
| D4 | Scope of this ticket | **This record only.** Implementation follows after sign-off |

D1, D2 and D4 are the repo owner's. D3 is the `oca-scout` verdict, evidence in §4.

## 1. The problem, measured

`_UAE_CURRENCY_USD_PEG` in `ncollection_account_localization_uae/models/res_company.py` seeds
seven currencies dated **`2020-01-01`**. Five are hard central-bank pegs and need no feed ever.
The module already names the other two itself:

```python
_UAE_INDICATIVE_CURRENCIES = frozenset({'KWD', 'EUR'})
```

Measured against the live ECB feed of **2026-08-03**:

| | Seeded (2020-01-01) | Today | Drift |
|---|---|---|---|
| 1 EUR in USD | `1.08` | **1.1535** | **+6.8%** |

So every EUR-denominated invoice in a UAE tenant converts **~6.8% wrong**, and has drifted
unattended for six years. That is the entire business case for #236 — and it is **two
currencies**, not seven.

## 2. D1 — Centralized fetch, config-sync push

```
admin DB ── 1 cron ── 1 outbound call ──> ECB
    │
    └── existing config-sync RPC (P2-T03) ──> tenant A, B, C … N
```

Rejected: a per-tenant Odoo cron. It makes N identical outbound calls, creates N egress points
to secure and audit, scales rate-limit/ban risk with tenant count, and — decisively — **would
require a tenant-egress security policy that does not exist** (§5).

The centralized shape reuses machinery that already shipped and is already hardened: config-sync
carries **per-tenant HMAC-SHA256 bearer keys** (#212), so a leaked key authenticates against one
tenant only, and every sync is logged with nightly reconciliation for drift.

## 3. D2 — ECB only, and exactly what that does and does not buy

**Measured, not assumed.** `eurofxref-daily.xml` is EUR-based and carries 29 currencies:

```
USD JPY CZK DKK GBP HUF PLN RON SEK CHF ISK NOK TRY AUD BRL CAD
CNY HKD IDR ILS INR KRW MXN MYR NZD PHP SGD THB ZAR
```

**AED, KWD, SAR, QAR, OMR and BHD are all absent.** This is the single most important fact in
this document, and it cuts two ways.

### It does not block EUR — the peg supplies the missing leg

AED is absent from the feed but it is *pegged*, so it does not need to be there:

```
ECB (live):     1 EUR = 1.1535 USD
fixed peg:      1 USD = 3.6725 AED     (_AED_PER_USD)
derived:        1 EUR = 4.236229 AED
Odoo rate row for an AED-base company: 1 / 4.236229 = 0.23605902
```

The cross is exact because one leg is a policy constant, not a market quote.

### It cannot fix KWD, and we are not pretending otherwise

KWD floats against an undisclosed basket and appears in **no** free official feed. ECB does not
publish it. After this work, **KWD remains an indicative 2020 snapshot.** The implementation must
surface that staleness rather than let a stale row look authoritative — see §6.

Rejected alternatives: **scraping CBUAE** (no API, breaks on any markup change, needs a ToS
review, and AED is pegged anyway so it buys nothing) and a **paid API** (recurring cost plus a new
secret to rotate, to refresh currencies that are mostly pegged — five of the seven seeded here are
hard pegs, and of the remaining two a paid feed would add only KWD).

## 4. D3 — BUILD, not ADOPT

`oca-scout` surveyed `OCA/currency` on `19.0`. Three findings drove the verdict.

**The issue's own premise was outdated.** There is no separate `currency_rate_update_ecb` on
19.0; OCA consolidated the provider into `currency_rate_update` via
`selection_add=[("ECB", …)]`. #236's text should be read with that correction.

**The reusable part is real but small; the rest fights our topology.**
`_obtain_rates(base_currency, currencies, date_from, date_to)` is genuinely separable — it takes
everything as parameters, writes nothing, and needs only a transient recordset. But `_update()`,
the layer that actually persists, does:

```python
record = CurrencyRate.create({
    "company_id": provider.company_id.id, ...
})
```

— company-scoped, **in the database it runs in**. There is no path there that reaches another
database, which is precisely what D1 requires. Adopting means calling `_obtain_rates` directly and
discarding the module's cron, retry and `last_successful_run` bookkeeping — i.e. discarding the
reasons to take a module at all, while still installing its models, wizards, views and ACLs.

**And it would crash on our own admin database.** `ncollection_billing`'s `_nc_ensure_currency`
sets the platform company to AED (*"Bill in AED"* — subscription invoices carry UAE VAT). Used as
designed, the provider defaults to `company_id = self.env.company`, so:

```python
provider._obtain_rates(provider.company_id.currency_id.name, …)   # → 'AED'
base_rate = float(content[k][base_currency])                      # KeyError: 'AED'
```

Every scheduled run, on the admin DB, forever — avoidable only by working *around* the module's
intended entrypoint, not by any switch it exposes.

Three supporting points:

- **Licence.** `OCA/currency` is AGPL-3, same as every repo already pinned, so no new category of
  risk. But all 14 `ncollection_*` addons are **LGPL-3**, so code *copied* from it could not be
  relicensed down — arguing for equivalent logic of our own rather than a verbatim port.
- **Our existing HTTP handling is stricter than theirs.** OCA uses a bare
  `urlopen(url, timeout=10)` with **no response-size cap**. `config_sync.py` already has bounded
  reads, a wall-clock deadline and `_MAX_RESPONSE_BYTES` — the exact class of gap #278 and #283
  were built to close. The fetcher should reuse those patterns.
- **Tenant install-set is untouched either way.** `CORE_TENANT_MODULES` never gains a currency
  module under D1, which removes ADOPT's biggest cost — but also collapses its value to *"is
  `_obtain_rates` worth installing a whole OCA model on the admin DB?"* It is not: ECB's feed is
  ~15 KB of flat XML, stable since 1999, and a stdlib `xml.etree` parser is 40–60 lines.

No `repos.yml` edit is proposed. Per Rule 5 this record **is** the required architecture check.

## 5. Security — the gap D1 closes for free

`ARCHITECTURE_SECURITY.md` today defines **no egress or outbound policy at all** (verified: zero
matches across the document). Under a per-tenant cron that would have been a blocker — N tenant
databases making outbound calls with no policy governing them.

Under D1 it nearly evaporates: **tenant databases make no outbound calls whatsoever**, and the
admin DB — already the most-audited, reachable only via the `admin.` subdomain — gains exactly one
allowlisted host.

**Applied** to §11 *Platform-Layer Specific Risks* on owner sign-off (2026-08-04). Reproduced
here so this record stays readable on its own; `ARCHITECTURE_SECURITY.md` is authoritative.

| Surface | Risks | Controls |
|---|---|---|
| **Outbound rate fetch** (#236) | Hostile/oversized response from an external host; feed outage silently freezing rates; a stale rate treated as authoritative | Admin DB only — **no tenant DB makes outbound calls**; single allowlisted host (`www.ecb.europa.eu`); bounded read + wall-clock deadline reusing `config_sync.py`'s hardened patterns (#278/#283); rates written only via the platform→tenant config-sync channel with its per-tenant HMAC keys (#212); fetch failure leaves the previous rate intact and raises an alert rather than writing a zero/partial row |

## 6. What the implementation must get right

Notes for the follow-up ticket, from reading the shipped code.

- **`_nc_setup_uae_currencies` must not be reused as the write path.** Its idempotency guard is
  coarse — *"skips a currency already rated for the root company"* — so once the static
  `2020-01-01` peg row exists it will never write again. A daily writer needs its **own** path
  producing **dated** rows.
- **Rates must be written to the ROOT company.** Odoo rejects rates on a branch company
  (`res.currency.rate._check_company_id`) and `_get_rates` filters on `(False, root_id)`.
- **Do not overwrite the pegs.** Only currencies in `_UAE_INDICATIVE_CURRENCIES` are refreshed;
  the five hard pegs stay as seeded.
- **Reuse the savepoint-per-risky-step, fail-soft pattern** already established in
  `_nc_setup_uae_currencies`, so one failure cannot poison the cursor or starve a batch.
- **Surface KWD's staleness explicitly.** A rate that cannot be refreshed must not look
  refreshed.

## 7. What this does NOT cover

- **KWD stays stale.** ECB does not publish it; no free official source does. This ships one of
  the two stale currencies, and says so rather than implying full coverage.
- **No code.** By D4. Nothing is fetched, written or scheduled by this document. The §11 row
  states the envelope the implementation must satisfy; it does not implement it.
- **Historical invoices are not restated.** Fresh rates apply going forward; the six years of
  drift already booked against the 2020 row are an accounting question, not a technical one.
- **No per-tenant rate overrides.** Every tenant receives the same platform-pushed rate.

## 8. What would reopen these decisions

- **Per-tenant overrides become a real requirement** — then OCA's per-company
  `res.currency.rate.provider` fits natively, installed in that tenant's DB only. Different
  topology, deserves its own scout pass.
- **A GCC/CBUAE source appears upstream** — it would then serve the peg side too, and every
  provider's `_get_supported_currencies()` is worth re-checking, not just ECB's.
- **Multiple providers with admin-configurable schedules** — the abstraction we are declining to
  adopt starts earning its weight; a single hardcoded ECB fetcher does not scale to that.
