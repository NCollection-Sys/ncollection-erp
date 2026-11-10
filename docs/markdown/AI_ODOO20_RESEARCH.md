# AI in Odoo 20 and what `ncollection_ai` does about it (research, 2026-11-10)

Scope: what Odoo 20 ships for AI, what a **Community** database gets, what may legally be reused, whether it fits our Odoo 19 Community architecture, and which capabilities
`ncollection_ai` implements on its own. No Enterprise code was read or copied. Every statement below says how it was checked.

## 1. What was checked, and how

| # | Question | Method | Result |
|---|---|---|---|
| 1 | Does the Community source tree contain an AI application? | GitHub API listing of `addons/` of `odoo/odoo` branches **19.0**, **20.0** and `master` (the public LGPL repository), 2026-11-10 | **No.** No directory named `ai`, `ai_*`, `llm*`, `agent*` or `mcp*` exists on any of the three branches. `mail_bot` (OdooBot, scripted replies) is the only chat-bot code. |
| 2 | Licence of the Community modules next to it | `addons/mail/__manifest__.py` on `20.0` / `master` | `'license': 'LGPL-3'` (version `1.19`). The Community repository is LGPL-3. |
| 3 | Does Odoo document AI features for 20? | Odoo documentation, `master` and `19.0` trees (`applications/productivity/ai`, `.../ai/mcp_server`) | Yes: pages **AI providers**, **AI MCP server**, **Available MCP tools** exist in the `master` documentation (not in `19.0`). The documentation does not label them Community. |
| 4 | Edition and pricing of those features | Third-party summaries of the Odoo 20 release notes (announced at Odoo Experience, 24 September 2026), 2026-11-10 | **Secondary sources only, not confirmed from Odoo's own text:** AI features are reported as Enterprise-only and consuming credits bought through In-App Purchase (IAP); agents can reach a database through an MCP server and have a *Create Records* skill. |

**Conclusion on availability.** Because the public Community repository contains no AI module on 19.0, 20.0 or master, **an Odoo 20 Community database has no native AI
application**. The AI layer is therefore either Enterprise-only or an IAP service; we could not read Enterprise sources (restricted) and did not try. Item 4 is
**unverified against a primary source**: confirm with Odoo (or the Enterprise licence terms) before quoting it to a customer.

## 2. What may legally be reused

* **Community (LGPL-3) code** may be reused and modified with the licence obligations (keep notices, share changes to the LGPL code itself). Nothing AI-specific exists there.
  What is reusable and relevant: the `mail`/`bus` plumbing and the web client (ListRenderer, OWL, Dialog) that we already extend.
* **Enterprise (OEEL) code**: not reusable. We did not copy, port or paraphrase it. Odoo's AI app, agents, "Ask AI" and MCP server are treated as **inspiration for
  capabilities only**; every implementation below is ours.
* **MCP (Model Context Protocol)** is a public protocol specification, not Odoo's property: speaking it from our own module does not use Odoo code.
* **Provider SDKs / APIs** (Anthropic, OpenAI, Google) are used through their public APIs under their own terms; the choice stays behind our gateway.

## 3. Compatibility with our architecture (Odoo 19 Community, database-per-tenant)

* Nothing from the Odoo 20 AI stack can be installed on Odoo 19 Community (no source, Enterprise-licensed, version-bound). There is nothing to "adopt"; the decision of
  `OCA_DEPENDENCIES.md` (build natively) stands. No new OCA dependency is introduced here.
* Our design already matches what such products do and keeps what a multi-tenant SaaS needs: the model never sees the database; a **read-only query registry** is
  run **as the asking user** (record rules and groups decide); PII is replaced by tokens before text leaves the tenant database; a **satellite gateway** owns the provider key,
  budgets and the circuit breaker; history is private per user; the answer is untrusted text (escaped, never HTML).

## 4. Capabilities worth having, and where each stands in `ncollection_ai`

| Capability seen in the market | Our status | Notes |
|---|---|---|
| Ask questions of your own business data (read-only) | **Done.** Topics: receivables, overdue, payables, sales, purchases, payments, stock; English and Arabic | `ai_topics.py`; runs as the user; no write path exists |
| Streaming answers, conversation history, per-user privacy, Arabic / RTL, animated bubble and panel, reduced motion | **Done and browser-verified** | |
| Say who answered (language model vs demonstration) | **Done (this release):** every assistant message stores `origin` = `model` or `demo` and the model id; the widget prints "AI model: <id>" or "Demo mode: a summary of your data, not an AI model" | prevents a mock from passing as a model |
| Provider adapters | Gateway has `mock` and `anthropic`. Adding another provider = one class in `satellites/ai_gateway/providers.py` + `NC_AI_PROVIDER` value | no second provider is built or tested; none was contacted |
| Question the user's uploaded file | **Not built.** Needs a safe text-extraction step and the same PII redaction | candidate next module slice |
| Agent that *creates* records from a request | **Deliberately not built.** A write path through an LLM needs an approval step, an audit trail and per-model allow-lists | revisit only with an owner decision; the invoice-amount audit and approvals modules already exist to build on |
| MCP server exposing tools to external agents | **Not built.** Design: the same read-only registry exposed as MCP tools behind `ncollection_api` OAuth2 scopes | needs a separate authorization review |

## 5. Three ways an answer can be produced (what the interface now distinguishes)

1. **AI model** (`origin = model`): the configured provider (`NC_AI_PROVIDER=anthropic` + key on the gateway) answered a sanitised prompt. Only this is conversational AI.
2. **Demo mode** (`origin = demo`): the built-in `mock` provider. It reaches nothing and understands nothing; when the prompt carries figures it states them
   (a deterministic summary of the user's own data), otherwise a canned sentence. Safe default of the gateway.
3. **Registry summaries**: the figures themselves are produced by the read-only query registry; both of the above only phrase them.

## 6. What a real-provider acceptance needs (not executed: no credential, no authorization)

Configuration on the gateway host only: `NC_AI_PROVIDER=anthropic`, `NC_AI_API_KEY`, `NC_AI_MODEL`, `NC_AI_GATEWAY_KEY` shared with Odoo, a
per-tenant token budget. In Odoo: `ncollection_ai` installed, the tenant's plan allowing AI, the user in a role the registry allows.

Acceptance test, to run once an owner authorizes a call to a named provider with a named key:

1. Ask "How much do customers owe us?" as an accountant: the answer states the figure the registry returns for that user, the badge reads **AI model: <model id>**, the gateway
   log shows `provider=anthropic`.
2. Ask the same as a user without receivables rights: the answer must not contain the figure (the registry returned nothing).
3. Names of partners in the question do not appear in the gateway request log (tokens only).
4. A second tenant cannot read the first tenant's history.
5. Disconnect the provider: the widget shows the error line, nothing is saved.
6. Exceed the budget: the gateway refuses before the first byte.

Until then the correct statement is: **implemented, verified with the mock provider, requires live provider acceptance.**
