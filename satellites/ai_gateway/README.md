# AI Gateway Satellite (P5-T02 / #59)

The single component on this platform permitted to call an LLM provider, and the
only one holding provider API keys.

It holds **no database credentials** — not the admin DB's, not any tenant's.

## Why it exists in this shape

`ARCHITECTURE_SECURITY.md` §11 records a shipped control, earned across
#236/#278/#283/#308/#309:

> **Admin DB only — no tenant DB makes any outbound call.**

An LLM feature is by construction outbound traffic carrying tenant business
data, so Phase 5 collides with that control on day one. The resolution
(`AI_PLATFORM_DESIGN.md` §1.2, Option C — which the architecture had already
specified in `ARCHITECTURE_DATA_PLATFORM.md` §10) is this satellite:

```
Tenant DB (ncollection_ai)          AI GATEWAY satellite          Internet
• builds context from ITS OWN  →    • LLM keys, NO DB creds  →    ONE allowlisted
  data, sanitises PII here            (receives, never fetches)    provider host
• cannot read another tenant's      • per-tenant budget, rate
  DB by construction                  limit, circuit breaker
        └─ Odoo public interfaces    • logs metadata, never bodies
```

**No database — tenant or admin — is in the egress path.**

## The isolation is structural, not configured

The compose service joins the egress plane (`default`) and deliberately **not**
`nc_dbplane`. Omitting credentials would depend on configuration staying correct
forever; omitting the network means the process has **no route to Postgres at
all**. `make ai-verify` asserts this against the running container:

```
✅ PASS: gateway cannot reach db:5432 (BLOCKED:gaierror)
```

If something appears to need a database connection here, the design already
considered and rejected that (§1.2, Option B: it makes the admin DB read every
tenant's ERP data — a two-layer violation and a worse security trade).

## Running it

```bash
make ai-up        # start (mock provider by default — reaches nothing)
make ai-verify    # prove the choke point and the DB isolation
make ai-test      # unit + HTTP tests; no Docker, no network
make ai-logs      # structured JSON logs (metadata only)
make ai-down
```

Odoo reaches it at `http://ai-gateway:8080` over the compose network. It is not
published to the host: exposing an endpoint that spends money on a metered API
serves no purpose.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/healthz` | satellite contract (§10) — status, active provider, circuit state |
| `POST` | `/v1/complete` | the choke point. `{"tenant": "...", "prompt": "...", "max_tokens": 1024}` |

### Errors are friendly by contract

| Status | `error` | When |
|---|---|---|
| `429` | `ai_budget_exhausted` | tenant is out of tokens — **the ticket's acceptance criterion**; never a 500 |
| `429` | `ai_rate_limited` | too many requests in the window |
| `503` | `ai_provider_unavailable` | circuit open — *"your data was not sent anywhere"* |
| `502` | `ai_provider_error` | provider failed; status code only, never its body |

## Configuration

| Variable | Default | Notes |
|---|---|---|
| `NC_AI_PROVIDER` | `mock` | `mock` \| `anthropic`. Mock reaches nothing |
| `NC_AI_API_KEY` | — | required for `anthropic`; fails at startup if empty |
| `NC_AI_MODEL` | `claude-sonnet-4-5` | |
| `NC_AI_TOKEN_BUDGET` | `100000` | per tenant, per window |
| `NC_AI_MAX_REQUESTS` | `60` | per tenant, per window |
| `NC_AI_WINDOW_SECONDS` | `3600` | |
| `NC_AI_BREAKER_THRESHOLD` | `5` | consecutive failures before the circuit opens |
| `NC_AI_BREAKER_RESET` | `60` | seconds before a half-open probe |

**`mock` is the default as a safety property**, not a placeholder: a
misconfigured deployment produces obviously-fake completions rather than
silently spending money or shipping tenant content somewhere unintended.

**Budgets reserve the worst case** (`prompt + max_tokens`) *before* calling — a
ceiling charged only on success is not a ceiling. A consequence worth knowing:
if `NC_AI_TOKEN_BUDGET` is below the default `max_tokens`, every request that
does not set `max_tokens` explicitly is refused on its first call. The gateway
logs a `config_warning` at startup when that is true.

## Two limitations, stated rather than buried

**Budget state is in memory, so a restart resets it.** The satellite has no
database — that is the entire point — so there is nowhere durable to keep
counters without handing this process the access the design removed. The hard
protection against a runaway bill is the provider-side spend cap, which no
restart resets. Durable budgets belong on the tenant side (P5-T03), which
already has a database inside the isolation boundary. **Do not "fix" this by
giving the satellite a database.**

**No real provider call has been exercised.** This repository holds no LLM API
key and must not. Everything is verified against `mock`. Switching to
`anthropic` is a config change plus uncommenting the provider host in
`config/hardening/egress_allowlist.txt` and re-running `harden.sh` — but *that
path is unproven until someone runs it with a real key*.

## Egress

Reaching a real provider requires its host in
`config/hardening/egress_allowlist.txt` **and** a re-run of
`scripts/deploy/harden.sh`. #311's `DOCKER-USER` backstop DROPs everything else
from this subnet, so a forgotten allowlist entry does not fail open — the
gateway reports the provider unreachable and the circuit opens.
