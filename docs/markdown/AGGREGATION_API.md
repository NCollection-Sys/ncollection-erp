# Aggregation API (P4-T01)

The tenant-side aggregation engine that powers every dashboard in Phases 4–5 and,
later, the AI context engine (P5-T03).

> **The rule this exists to enforce** — `ARCHITECTURE_DATA_PLATFORM.md` §9:
> *"the P4-T01 engine is the single choke point for dashboard queries: `ormcache`
> or Redis with explicit invalidation on source writes. **No widget queries
> models directly.**"*
>
> If a widget calls `_read_group` itself, it is uncached, unbudgeted and
> invisible to this document. Don't.

- **Model**: `ncollection.aggregation.engine` (AbstractModel, tenant-side)
- **Lives in**: `ncollection_core` — already in `CORE_TENANT_MODULES`, so every
  tenant has it with no provisioning change and no fleet backfill
- **Source**: `custom_addons/ncollection_core/models/aggregation/`

---

## 1. Calling it

```python
engine = env['ncollection.aggregation.engine']

result = engine.aggregate({
    'key': 'revenue_6m',
    'model': 'sale.order.line',
    'domain': [('order_id.date_order', '>=', six_months_ago)],
    'groupby': ['order_id.date_order:month'],
    'aggregates': ['price_total:sum'],
})
# -> {'key': 'revenue_6m', 'rows': [...], 'cached': False}
# -> or None
```

Batch form, for a dashboard payload:

```python
results = engine.aggregate_many([spec_a, spec_b, spec_c])
# -> {'revenue_6m': [...], 'top_customers': [...]}
```

### Spec fields

| Field | Type | Required | Meaning |
|---|---|:--:|---|
| `key` | str | ✅ | Identifies the result to the caller |
| `model` | str | ✅ | Technical model name, e.g. `sale.order.line` |
| `domain` | list | | Odoo domain (default `[]`) |
| `groupby` | list | | `_read_group` groupby specs |
| `aggregates` | list | | e.g. `['price_total:sum', '__count']` |
| `limit` / `offset` / `order` | | | Passed through to `_read_group` |
| `cache` | bool | | `False` forces a live read (default `True`) |

At least one of `groupby` / `aggregates` is required.

---

## 2. The two return contracts that matter

### `None` means "no result for you"

`aggregate()` returns `None` — never raises — when the model is not installed,
the plan does not license it for this user, the spec is malformed, or the read
failed. All four collapse into one answer because for a dashboard they mean the
same thing: don't render this tile.

`aggregate_many()` **omits** dropped specs rather than mapping them to `None`.
That is deliberate: a consumer must never have to distinguish "present but
empty" from "absent", because that distinction is exactly how license state
leaks into a UI.

### Consumers must not re-implement licensing

A model raising `AccessError` is dropped, which is P1-T10 Ring 2 doing its job.
The engine never reads the plan itself. **Do not** add a plan check in a widget
"to be safe" — two rings that each decide licensing independently drift apart,
and the drift is silent.

---

## 3. Caching and invalidation

Two tiers, keyed by a source-model version counter.

| Tier | Where | Purpose |
|---|---|---|
| Version counters | `ncollection.aggregation.version` (one small row per tracked model) | The invalidation signal |
| Process cache | module-level dict in `cache.py`, TTL `300s`, capped at `512` entries | The fast path |

A write to a tracked model bumps its counter. The counter is part of every cache
key, so bumping it makes every entry derived from that model unreachable **in
every worker at once** — no key index, no cross-worker delete.

### Why not `ormcache`

`ormcache(cache=...)` can only name one of six groups hardcoded in
`odoo/orm/registry.py` (`_CACHES_BY_KEY`); a custom name raises `KeyError` in
`Registry.clear_cache` and has no `orm_signaling_<name>` table. Verified on this
build. That leaves `default` — which is where P1-T10's
`_ncollection_blocked_access_cached` lives, so invalidating aggregations through
`clear_cache()` would **flush the Ring 2 license cache on every ERP write**: a
performance regression on a security-critical path. Hence a private cache.

**Never call bare `registry.clear_cache()` from aggregation code.**

### The cache key includes `uid` **and a context fingerprint**

Record rules and Ring 2 both make an aggregate user-dependent, so a shared entry
would serve one user's figures to another — a data leak inside a tenant, not a
staleness annoyance. Keying by group set would hit more often, but per-user
record rules (`user_id = uid` domains are routine) make groups an unsound proxy.
Locked in by `test_cache_is_keyed_by_user`.

**`uid` alone is not enough.** Multi-company security in Odoo is row-level,
through `ir.rule` domains like `[('company_id', 'in', company_ids)]` where
`company_ids` derives from `allowed_company_ids` in the context. One user
switching active company keeps the same `uid` while their legitimate result
changes entirely. The first version of this cache omitted company scope and
would have served Company A's receivables on Company B's dashboard for up to the
300s TTL — silently. Caught in review before merge.

So the key also folds in `context_fingerprint(env)`:

| Context key | Why it changes the answer |
|---|---|
| `allowed_company_ids` | row-level company `ir.rule` filtering |
| `active_test` | whether archived rows are counted |
| `tz` | bucket boundaries for `groupby: ['date:month']` |
| `lang` | translated `display_name` baked into cached rows |

Company ids are sorted, so `[A, B]` and `[B, A]` share an entry. Per-request
noise (`params`, `bin_size`) is deliberately excluded — hashing the whole context
would shred the hit rate without affecting correctness.

**If you add a spec whose result depends on some other context key, add that key
to `context_fingerprint` in the same PR.** Locked in by
`test_cache_is_keyed_by_company` and `test_cache_is_keyed_by_result_changing_context`.

### Tracked models

Writes to these invalidate derived aggregations
(`AGGREGATION_SOURCE_MODELS` in `version.py`):

`sale.order` · `sale.order.line` · `account.move` · `account.move.line` ·
`account.payment` · `stock.move` · `stock.move.line` · `stock.picking` ·
`stock.quant` · `hr.employee` · `hr.leave` · `hr.attendance` · `crm.lead` ·
`purchase.order` · `purchase.order.line` · `mail.activity` · `res.partner` ·
`product.product` · `product.template`

> ⚠️ **Aggregating a model outside this list is supported but TTL-bounded** —
> its cached result is *not* invalidated on write and can be up to 300s stale.
> If a dashboard needs write-accurate numbers over another model, add it to
> `AGGREGATION_SOURCE_MODELS`. Each entry costs one frozenset membership test
> per write to that model, which is why the list is curated rather than
> "everything".

---

## 4. Performance budget

**Target**: every dashboard endpoint under **500ms** on 100k-record data
(`ARCHITECTURE_DATA_PLATFORM.md` line 267; the platform-wide interactive SLO at
line 38 is p95 < 500ms).

```bash
make agg-bench          # 100k rows, writes scripts/perf/results/aggregation.json
make agg-clean          # drop the aggbench fixture
AGG_BENCH_ROWS=10000 make agg-bench   # faster smoke run
```

The budget is held against the **cold** (cache-bypassed) number, not the warm
one. A warm cache flatters the result and hides a regression in the underlying
query.

**What the benchmark does and does not prove.** It measures query time versus
data volume, single-process, on an otherwise-idle stack. It does **not** measure
concurrency — that is P3-T03 (`scripts/perf/run_load_test.sh`, k6). The two are
different axes and neither substitutes for the other. The stack must be idle
while it runs; one Postgres is shared with every suite.

**Fixture ownership** (CLAUDE.md / R-004): this suite owns the `agg*` namespace
and nothing else. Both the runner and the seeder hard-refuse a database whose
name does not start with `agg`.

---

## 5. Boundaries

**ADR #15 / FPA §7 — aggregation only, never financial computation.** This
engine caches and fans out; it does not decide what a number *means*. Financial
figures are owned by `ncollection_account_reports` (#111, merged) and
`ncollection_account_analytics` (#120, **still open**). Financial specs carry a
`HANDOFF` marker, as the P1-T17 providers already do. When #120 lands, financial
aggregations source from it rather than growing computation here.

**Soft dependencies.** `ncollection_core` depends on `base`, `web` and
`ncollection_branding` only. The engine reaches `sale`/`account`/`stock`/`hr`
through registry and read probes, never through `depends`, so it cannot force an
app into a tenant's module set and leaves P1-T09 menu visibility and P1-T10
enforcement untouched. **Do not add any of those to `depends`.**

**Two-layer separation (Rule 3).** The engine is tenant-side. Platform addons
(`ncollection_saas`, `ncollection_subscription`) must not call it directly —
cross-layer access goes through RPC.

---

## 6. Consumers

| Task | Issue | Status |
|---|---|---|
| KPI logic models (`ncollection.kpi`) | #55 (P4-T02) | open — builds on this |
| CEO dashboard | #56 (P4-T03) | open |
| Department dashboards | #57 (P4-T04) | open |
| AI context injection | P5-T03 | later phase |
| Customer workspace dashboard | #54 (P1-T17 migration) | migrated onto this engine |
