# -*- coding: utf-8 -*-
"""P4-T01 query performance budget — measured, not asserted in prose.

Run inside an Odoo shell against a seeded ``agg*`` database:

    docker compose exec odoo odoo shell -d aggbench --db_host=db \\
        --db_user=odoo --db_password=odoo --no-http \\
        < scripts/perf/bench_aggregation.py

Emits a table to stdout and writes ``scripts/perf/results/aggregation.json`` so
a regression is a diff, not a memory. The budget is
ARCHITECTURE_DATA_PLATFORM line 267: dashboard endpoints < 500ms.

**What this measures and what it does not.** Every number is single-process,
cold vs warm cache, on the dev box. It answers "is the aggregation path within
budget at 100k rows", which is #54's acceptance. It does NOT answer "does the
system hold up under N concurrent users" — that is P3-T03's k6 harness
(``run_load_test.sh``), a different axis. Quoting one to answer the other is how
a performance claim becomes fiction.

The stack must be otherwise idle: one Postgres is shared with every other suite,
so a concurrent run makes these numbers meaningless.
"""

import json
import os
import statistics
import time
from datetime import date, timedelta

BUDGET_MS = 500.0
REPEATS = int(os.environ.get('AGG_BENCH_REPEATS', 5))

# Results travel out on stdout between these markers, NOT via a file: only
# ./custom_addons is bind-mounted into the container (/mnt/extra-addons), so
# scripts/perf/results/ does not exist in here. run_aggregation_bench.sh
# captures the block and writes it host-side.
JSON_BEGIN = '---AGGBENCH-JSON-BEGIN---'
JSON_END = '---AGGBENCH-JSON-END---'


def _specs():
    """The aggregation shapes a real dashboard issues.

    Deliberately mirrors what the P1-T17 dashboard already computes plus the
    grouped/time-series shapes P4-T03/T04 will need, so the budget is measured
    against realistic work rather than a trivial COUNT.
    """
    today = date.today()
    six_months_ago = today - timedelta(days=182)
    return [
        {
            'key': 'order_count',
            'model': 'sale.order.line',
            'domain': [],
            'aggregates': ['__count'],
        },
        {
            'key': 'revenue_total',
            'model': 'sale.order.line',
            'domain': [],
            'aggregates': ['price_total:sum'],
        },
        {
            'key': 'revenue_6m_by_month',
            'model': 'sale.order.line',
            'domain': [('order_id.date_order', '>=', six_months_ago)],
            'groupby': ['order_id.date_order:month'],
            'aggregates': ['price_total:sum'],
        },
        {
            'key': 'top_customers',
            'model': 'sale.order.line',
            'domain': [],
            'groupby': ['order_partner_id'],
            'aggregates': ['price_total:sum'],
            'order': 'price_total desc',
            'limit': 10,
        },
        {
            'key': 'by_product',
            'model': 'sale.order.line',
            'domain': [],
            'groupby': ['product_id'],
            'aggregates': ['product_uom_qty:sum', '__count'],
        },
    ]


def _time_call(engine, spec, use_cache):
    payload = dict(spec, cache=use_cache)
    started = time.perf_counter()
    result = engine.aggregate(payload)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return elapsed_ms, result


def run(env):
    engine = env['ncollection.aggregation.engine']
    from odoo.addons.ncollection_core.models.aggregation import cache as agg_cache

    row_count = env['sale.order.line'].search_count([])
    print('\n=== P4-T01 aggregation budget — %d sale.order.line rows ===\n'
          % row_count)
    print('%-24s %12s %12s %10s %8s' % (
        'spec', 'cold p50 (ms)', 'warm p50 (ms)', 'budget', 'verdict'))
    print('-' * 72)

    results = {}
    all_pass = True
    for spec in _specs():
        # Cold: cache bypassed entirely, so this is the raw ORM path.
        cold = []
        for _ in range(REPEATS):
            agg_cache.clear()
            elapsed, result = _time_call(engine, spec, use_cache=False)
            cold.append(elapsed)
        if result is None:
            print('%-24s %12s %12s %10s %8s'
                  % (spec['key'], 'n/a', 'n/a', 'n/a', 'SKIP'))
            results[spec['key']] = {'skipped': True}
            continue

        # Warm: first call populates, the rest must hit.
        agg_cache.clear()
        _time_call(engine, spec, use_cache=True)
        warm = []
        for _ in range(REPEATS):
            elapsed, warm_result = _time_call(engine, spec, use_cache=True)
            warm.append(elapsed)
        cache_hit = bool(warm_result and warm_result.get('cached'))

        cold_p50 = statistics.median(cold)
        warm_p50 = statistics.median(warm)
        # The budget applies to what a dashboard actually experiences. A cold
        # miss is the honest worst case, so hold THAT to the budget, not the
        # flattering warm number.
        passed = cold_p50 < BUDGET_MS
        all_pass = all_pass and passed
        print('%-24s %12.1f %12.3f %10s %8s' % (
            spec['key'], cold_p50, warm_p50, '<%dms' % BUDGET_MS,
            'PASS' if passed else 'FAIL'))
        results[spec['key']] = {
            'cold_p50_ms': round(cold_p50, 3),
            'cold_max_ms': round(max(cold), 3),
            'warm_p50_ms': round(warm_p50, 3),
            'speedup': round(cold_p50 / max(warm_p50, 0.0001), 1),
            'cache_hit_confirmed': cache_hit,
            'within_budget': passed,
        }
        if not cache_hit:
            print('   ^ WARNING: warm call was not served from cache')

    payload = {
        'generated_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'database': env.cr.dbname,
        'sale_order_line_rows': row_count,
        'budget_ms': BUDGET_MS,
        'repeats': REPEATS,
        'measurement_scope': (
            'single-process, dev box, otherwise-idle stack; query-time vs data '
            'volume. Concurrency is P3-T03 (k6), a different axis.'),
        'specs': results,
        'all_within_budget': all_pass,
    }
    print(JSON_BEGIN)
    print(json.dumps(payload, indent=2, sort_keys=True))
    print(JSON_END)

    print('\nVERDICT: %s\n' % ('ALL WITHIN BUDGET' if all_pass else 'BUDGET EXCEEDED'))
    return payload


if 'env' in dir():
    run(env)  # noqa: F821 - provided by odoo shell
