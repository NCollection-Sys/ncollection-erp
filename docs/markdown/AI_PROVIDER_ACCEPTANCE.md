# AI Assistant: readiness and live-provider acceptance

Owner decision 2026-11-16: **OpenAI is the first real provider**, behind the existing gateway abstraction. No real key,
no paid call and no tenant data has been sent anywhere yet. Until the checklist below is signed off, every workspace runs
in **Demo** mode, and the screens say so.

## Six readiness states (Workspace Administration > AI Assistant)

| # | State | How it is checked | Fixed by |
|---|---|---|---|
| 1 | Gateway reachable | `GET /healthz` | deployment (compose `ai-gateway`) |
| 2 | Gateway authenticated | signed `POST /v1/auth-check`: identity accepted, provider **not** called, nothing spent | the same `NC_AI_GATEWAY_KEY` on Odoo and on the gateway (an internal service secret, not a provider key) |
| 3 | Business-data tools | read-only topics whose models are installed and readable by this user | install the apps |
| 4 | Real provider | gateway reports a provider other than `mock` | `NC_AI_PROVIDER=openai`, `NC_AI_API_KEY`, `NC_AI_MODEL` on the gateway only |
| 5 | Free-typed questions | `ncollection_ai.enable_free_text_questions` | Workspace Administrator, button on the screen; refused while 1 to 3 are not green |
| 6 | External use authorised | `ncollection_ai.external_provider_authorized = True` | Platform Administrator only, after this checklist |

Mode: **Unavailable** (1 to 3 not all green), **Demo** (mock provider or 6 not given), **Live** (real provider AND 6).
An ordinary user only ever sees "The AI assistant is not available on this workspace yet. Nothing was sent."

## What a question costs (measured on a copy of `ncollection`, mock provider)

| Question | Input tokens | Output tokens |
|---|---|---|
| Revenue by month | 513 | 71 |
| Bank balance and pending | 579 | 75 |
| Arabic bank balance | 575 | 73 |

A real model answers in prose, so expect roughly 500 to 1,500 input and 150 to 400 output tokens per question.
**Cost per question = input tokens × input price + output tokens × output price**, at the provider's published per-token
prices on the acceptance date (not recorded here: prices change, and a figure in a document would go stale silently).
The gateway's per-tenant token budget (`NC_AI_TOKEN_BUDGET`, refreshed per window) and rate limit cap the spend per
workspace whatever the price; budget exhaustion is a friendly 429, never a 500.

## Models

* **Default (recommended to start):** a small, inexpensive OpenAI chat model, configured as `NC_AI_MODEL`.
  `gpt-4.1-mini` is the adapter's default. **Confirm it is still offered, and its price, on OpenAI's model page
  on the acceptance date.**
* **Higher quality (optional):** the provider's current flagship chat model, for workspaces that pay for it, by
  setting `NC_AI_MODEL` on the gateway. The model id is configuration, never code.

## What leaves the platform in Live mode

The question, and a context the tenant database builds from **its own** data: aggregate figures (sums by month,
partner, journal, product) read **as the asking user**, with partner names pseudonymised by `ncollection.ai.pii`
before the prompt is built. Never raw records, never another tenant's data (the gateway holds no database credentials
and has no route to Postgres). This is still business data sent to a third party: the owner, and each tenant's terms,
must accept that before state 6 is set.

## Live-provider acceptance checklist

1. Create an OpenAI project key **for the gateway only**; store it as a secret on the VPS (never in git, never in
   Odoo): `NC_AI_API_KEY`. Set `NC_AI_PROVIDER=openai` and `NC_AI_MODEL`.
2. Set a project-level monthly spend limit at the provider; set `NC_AI_TOKEN_BUDGET` per tenant.
3. Restart the gateway only; `GET /healthz` must report `openai`.
4. On ONE internal test workspace: AI Readiness shows 1 to 4 green, 6 still off.
5. Platform Administrator sets state 6 on that workspace only; ask the six standard questions (revenue, expenses,
   bank and cash, pending, reconciliation, top products) in English and Arabic; compare every figure with the
   corresponding report.
6. Check gateway logs: metadata only, no prompt or answer body.
7. Only then enable further workspaces, one decision each.

## Tests

* Gateway (stdlib, no network): 62 tests, including the OpenAI adapter (selection, key only in the header,
  completion and stream parsing, error type never body) and the auth-check probe (no tokens spent; wrong key,
  forged tenant and unsigned all refused).
* `ncollection_ai`: 173 tests, including the new topics, "revenue = posted invoices, not orders or receipts",
  every topic a bounded `read_group`, readiness unavailable without a gateway, admin-only, and free text refused
  until the backend is ready.
