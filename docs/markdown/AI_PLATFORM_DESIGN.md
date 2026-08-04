# AI Platform Design — P5-T01 Spike (#58)

**Status:** proposed — **requires owner approval before any Phase-5 implementation ticket starts**
(P5-T01 acceptance: *"design doc approved before any AI implementation task starts"*).
**Deliverable of:** [#58] `[P5-T01] LLM Provider Evaluation & Design Spike` · DEV-1 · 3 days · no production code.
**Binds:** P5-T02 (Gateway), P5-T03 (Context Injection), P5-T04 (Anomaly Detection), P5-T05 (NL→Domain), P5-T06 (Chat Widget), P5-T07 (Smart Search).

> **On volatile facts.** Provider pricing, model names, context windows and region availability change
> faster than this document will be revised. Everything in §2 marked ⏳ **must be re-verified at
> P5-T02 kickoff** — the *decision framework* is the durable part, not the numbers. A design doc that
> silently ages into wrong pricing is worse than one that says where it will rot.

---

## 1. The decision that governs everything else

### 1.1 The conflict, stated plainly

`ARCHITECTURE_SECURITY.md` §11 records a **shipped control**, earned across #236 / #278 / #283 / #308 / #309:

> **Admin DB only — no tenant DB makes any outbound call.**

An LLM feature is, by construction, outbound traffic carrying **tenant business data**. P5-T03 says the
context engine *"builds tenant-scoped context for prompts from ERP data"*; P5-T02 makes the gateway
*"the single choke point for ALL LLM calls"*. Taken naively, Phase 5 breaks §11 on day one.

Per CLAUDE.md ("if a requested implementation appears to conflict with `ARCHITECTURE_SECURITY.md`,
STOP and ask"), this is escalated rather than absorbed.

### 1.2 Options considered

**Option A — tenant DBs call the provider directly.** Rejected.
Multiplies the egress surface by N tenants, puts provider API keys in every tenant database, and
deletes a control the platform spent five tickets hardening. It also makes P5-T03's own acceptance
("the context builder physically cannot read another DB") harder to argue, not easier.

**Option B — admin DB builds the context and calls out.** Rejected.
Preserves §11 literally, but the admin database would need to *read every tenant's ERP data* to build
prompts. That is a direct two-layer violation (CLAUDE.md Rule 3: platform addons must not query tenant
ERP models directly) and concentrates every tenant's business data in the platform's highest-value
target. Trading a security control for a worse one.

**Option C — split the responsibility. ✅ RECOMMENDED.**

```
  Tenant DB                                   Admin DB                    Internet
  ─────────                                   ────────                    ────────
  ncollection_ai_client                    ncollection_ai gateway
  • builds context from ITS OWN data       • per-tenant budget + rate limit
  • sanitises PII HERE, pre-transit    →   • holds provider credentials     →   ONE allowlisted
  • cannot see another tenant's DB         • the ONLY outbound egress            provider host
    (by construction, not by policy)       • logs metadata, never bodies
              │                                        ▲
              └──── internal, authenticated ───────────┘
                    (per-tenant HMAC, the #212 config-sync pattern)
```

**Why this is the right shape:**

- **§11 holds exactly.** No tenant database makes an outbound call. The admin DB remains the single
  egress point — the identical topology as the #308 ECB rate fetch, so this is a *precedent applied*,
  not an exception carved.
- **Rule 3 holds.** The platform never reads tenant ERP models; the tenant hands over a finished,
  sanitised context payload.
- **P5-T03's acceptance becomes structural.** "The context builder physically cannot read another DB"
  is *free* when the builder runs inside the tenant database. It is a property of where the code
  lives, not a test that has to keep proving a negative.
- **The transport already exists.** The platform→tenant config-sync channel (#212) has per-tenant HMAC
  keys, a re-key path (#283), and hardened bounded reads. Phase 5 inherits it instead of inventing a
  second authenticated channel.

**What Option C costs, stated honestly:** the admin DB now *transits* tenant business content, even if
it never persists it. That is a real concentration of exposure and the reason for the controls in §5.

### 1.3 ⚠️ This needs your signature

Option C does not violate §11, but it does **extend** it: §11 currently describes one allowlisted host
fetching a public exchange rate. It would now also describe a host receiving tenant-derived prompt
content. **§11 must be amended before P5-T02 is written**, and that amendment is an architecture
change requiring owner approval — not something an implementation ticket may assume.

---

## 2. Provider evaluation

⏳ *Re-verify every figure in this section at P5-T02 kickoff.*

### 2.1 What actually matters here

The market ranks providers on benchmarks. This platform's constraints rank them differently:

| Criterion | Weight | Why it dominates here |
|---|---|---|
| **Data residency (UAE/GCC)** | Critical | `ARCHITECTURE_SECURITY.md` §11 lists in-region data as a market expectation; UAE SMB buyers ask early |
| **Contractual no-training guarantee** | Critical | Prompts carry customer financial data. Without it, this feature is unsellable |
| **Provider abstraction cost** | High | P5-T02 mandates swap-by-config; a provider with an eccentric API raises the abstraction's cost permanently |
| **Structured output reliability** | High | P5-T05 (NL→Domain) turns prose into Odoo domains. Malformed output is a failed feature, not a retry |
| **Cost per tenant at SMB scale** | High | 5–100 users. Margin is thin; a per-seat AI cost that scales linearly kills the plan |
| Raw benchmark score | Medium | Real workloads are summarisation and structured extraction, not frontier reasoning |

### 2.2 Candidates

| Provider | Residency | No-training | Structured output | Notes |
|---|---|---|---|---|
| **Anthropic (Claude)** ⏳ | No UAE region direct | Yes, on API terms | Strong; tool-use is reliable | Best structured-output behaviour of the three; direct API only |
| **OpenAI** ⏳ | No UAE region direct | Yes, on API terms | Strong; JSON mode mature | Widest ecosystem, most third-party examples |
| **Azure OpenAI** ⏳ | **UAE North exists** ⏳ | Enterprise terms | Same models as OpenAI | The residency answer, if the region carries the needed models — **verify model availability per region, not just region existence** |
| **AWS Bedrock** ⏳ | **me-central-1 (UAE)** ⏳ | Enterprise terms | Multi-model incl. Claude | Second residency route; adds AWS coupling |
| Self-hosted open models | Total | N/A | Weaker structured output | Rejected for now — GPU cost and ops burden are wrong for this team's size at this stage |

### 2.3 Recommendation

**Build against an abstraction; default to Anthropic Claude; treat Azure OpenAI (UAE North) or Bedrock
(me-central-1) as the residency escape hatch.**

The provider choice is deliberately *not* load-bearing, because P5-T02 already mandates swap-by-config.
What is load-bearing is that **the abstraction is written before a provider is chosen**, so the first
provider's quirks never leak into callers. Choosing Claude first is a preference, not a commitment.

**Residency is not decided here and must not be assumed decided.** In-region hosting is a *market
expectation* per §11, and the enabling ticket is **P10-T05 — Phase 10, five phases away**. So:

- Phase 5 ships with a non-regional provider and a **documented, customer-visible statement** of where
  prompt data is processed.
- Any tenant contract promising in-region processing is **blocked on P10-T05**, not on Phase 5.
- P5-T02's provider abstraction must make a region swap a config change, so P10-T05 does not become a
  rewrite.

If in-region processing is required for the *first* paying customer (#53), that reorders the roadmap
and is an owner decision, not an implementation detail.

---

## 3. Prompt architecture

**Three layers, assembled by the gateway, never by a caller:**

1. **System layer** — platform-owned, version-pinned, never tenant-editable. Establishes role, refusal
   boundaries, and output contract.
2. **Context layer** — tenant-built and PII-sanitised (see §5), size-capped before transit.
3. **Task layer** — the feature's own instruction (summarise, extract a domain, explain a variance).

**Templates are code, not data.** They live in the addon, versioned in git, and every logged interaction
records the template version. A prompt whose behaviour cannot be reproduced from a commit is not
debuggable, and financial-adjacent output must be explainable.

**Structured output is mandatory where the result drives behaviour.** P5-T05 (NL→Domain) must return a
validated schema and the result must be **parsed and validated before use** — an Odoo domain from an
LLM is untrusted input and gets the same treatment as any user input (Rule: validate at boundaries).

---

## 4. Token budgets and cost model

Plan tiers today: **STARTER (5 users) · GROWTH (25) · ENTERPRISE (100)**.

⏳ Initial values — calibrate against real usage in the first month; they are starting points, not
entitlements to defend:

| Tier | Monthly token budget | Rationale |
|---|---|---|
| STARTER | 200k | Enough to try the feature; not enough to run a business on it |
| GROWTH | 1M | ~40k/user — a few AI interactions per user per working day |
| ENTERPRISE | 5M | ~50k/user, with headroom for Smart Search (P5-T07) |

**Budget exhaustion returns a friendly, actionable error** (P5-T02 acceptance) — never a silent
degradation and never a surprise invoice. The budget is enforced **in the gateway**, because that is
the only choke point that sees every call.

**Cost control levers, in order of preference:** cache identical context+task pairs → cap context size
before transit → prefer smaller models for classification-shaped tasks → only then reduce budgets.

**A hard cost ceiling per tenant is required before launch.** A runaway loop against a metered API is a
financial incident, not a bug. This is the AI equivalent of the #310 finding (one stalled outbound call
starving every cron) — the failure mode is resource exhaustion, and it needs a limit that is not
"we'll notice".

---

## 5. PII handling

**Sanitise tenant-side, before transit.** This is the whole reason context is built in the tenant DB.
Once data reaches the gateway it has already left the tenant boundary; scrubbing there would be theatre.

**Baseline policy:**

- **Never send:** passwords, API keys, HMAC secrets, full bank/IBAN numbers, national ID numbers.
- **Pseudonymise by default:** partner names, emails, phone numbers — replaced with stable per-request
  tokens (`PARTNER_1`) and re-hydrated in the response tenant-side. The model reasons over structure;
  it does not need real identities.
- **Send freely:** amounts, dates, account types, aggregates, document states — the actual substance.
- **The gateway logs metadata only:** tenant, template version, token counts, latency, outcome.
  **Never prompt or completion bodies.** This mirrors `ncollection_auth`'s existing stance (#219/#261),
  where PII is minimised at 180 days and the pseudonymous remainder deleted at 400 — Phase 5 should not
  invent a second, weaker retention story.
- **Contractual no-training is a hard requirement**, not a preference, and must be verified per provider
  and per region before P5-T02 ships.

**Cross-tenant isolation is structural, not procedural.** The context builder runs inside one tenant
database and has no route to another. P5-T03's acceptance ("injection tests prove no cross-tenant data
can enter a prompt") is then a test that the *structure* holds, not a test fighting a shared component.

---

## 6. Streaming vs batch

| Surface | Mode | Why |
|---|---|---|
| P5-T06 AI Chat Widget | **Streaming** | Perceived latency dominates; a 6-second silent wait reads as broken |
| P5-T07 Smart Search | **Batch** | Sub-second structured result; streaming a domain expression helps nobody |
| P5-T05 NL→Domain | **Batch** | Output is validated as a whole before use — a partial domain is not a domain |
| P5-T04 Anomaly Detection | **Batch, async** | A background job. Note: **P5-T04 depends on P4-T01, not on this spike** — it is statistical, and does not need to wait |

**Streaming has an infrastructure consequence.** A held-open connection through the admin gateway
interacts with the #310 finding directly: `max_cron_threads = 1` and long-lived outbound calls are a
known bad combination on this platform. **P5-T02 must not implement streaming on the cron worker.**
Whether #310 is resolved first is an explicit prerequisite question, not an afterthought.

---

## 7. Reuse decision (Rule 2 / Rule 5)

`oca-scout` survey result: **recorded below at PR time.** No new dependency may be proposed without it.

Prior art already in this repo that Phase 5 **must reuse rather than reinvent**:

- **Hardened outbound HTTP** — bounded reads, wall-clock deadlines, `allow_redirects=False`, refusal
  rather than unbounded fallback: `ncollection_saas/models/config_sync.py` and `exchange_rate.py`
  (#278/#283/#308/#309). The gateway needs exactly this and must not hand-roll a third copy.
- **Per-tenant authenticated channel** — config-sync's HMAC keys and re-key path (#212/#283).
- **Encrypted credential storage** — whatever pattern config-sync uses for per-tenant keys should
  extend to provider API keys rather than introduce a second scheme.

---

## 8. What this design does NOT cover

- **It does not choose a region or promise residency.** That is P10-T05. Phase 5 ships with a
  documented processing location and no in-region guarantee.
- **It does not authorise the §11 amendment.** §11 must be updated to describe prompt-bearing egress
  *before* P5-T02 is written. That edit needs owner approval.
- **It does not resolve #310 or #311.** Both are open, both bear directly on Phase 5: #310 (cron
  starvation) constrains streaming; #311 (no network-level egress backstop) means the "single
  allowlisted host" control is still enforced by code review alone. **#311 should be a hard
  prerequisite for P5-T02** — a second egress with no network backstop is worse than the first.
- **It does not include benchmark results against this repo's data.** A meaningful evaluation needs a
  populated tenant (`make demo-tenant`, Al Barari Trading) and a fixed question set. Recommended as a
  short follow-up before P5-T02 rather than pretending the desk comparison in §2 is empirical.
- **No production code**, per the ticket.

---

## 9. Open questions for the owner

1. **Approve Option C** (tenant-side context, admin-side egress) as the Phase-5 topology?
2. **Approve amending §11** to describe prompt-bearing egress, before P5-T02 starts?
3. **Is in-region processing required for the first paying customer (#53)?** If yes, P10-T05 moves
   ahead of Phase 5 and this roadmap reorders.
4. **Is #311 (egress backstop) a hard prerequisite for P5-T02?** Recommended: yes.
5. **Are the §4 token budgets acceptable as starting values**, given they are guesses until real usage
   exists?
