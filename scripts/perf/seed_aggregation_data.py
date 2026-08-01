# -*- coding: utf-8 -*-
"""Bulk data generator for the P4-T01 performance budget.

Run inside an Odoo shell against a throwaway ``agg*`` database:

    docker compose exec odoo odoo shell -d aggbench --db_host=db \\
        --db_user=odoo --db_password=odoo --no-http \\
        < scripts/perf/seed_aggregation_data.py

Why this exists: #54's acceptance is "every dashboard endpoint under 500ms on
100k-record demo data", and nothing in the repo could produce 100k records.
``scripts/demo/seed_demo_data.py`` seeds a realistic *small* workspace (the Al
Barari demo tenant) and P3-T03's k6 harness measures HTTP concurrency, not query
time over data volume. Those are different axes; neither answers this one.

Fixture ownership (CLAUDE.md / R-004): this generator targets the **agg\\***
namespace only and refuses to run against any other database. ``rt*``, ``e2e*``,
``prov*`` and ``loadtest*`` belong to other suites and dropping their fixtures
silently destroys another suite's run.

Scale is controlled by ``AGG_BENCH_ROWS`` (default 100000, the acceptance
number). Rows are written with ``create()`` in batches rather than raw SQL: the
benchmark must measure queries against data Odoo itself would produce —
stored computes, indexes and all — or the number proves nothing about a real
dashboard.
"""

import logging
import os
import random
import time
from datetime import date, timedelta

_logger = logging.getLogger(__name__)

TARGET_ROWS = int(os.environ.get('AGG_BENCH_ROWS', 100000))
BATCH_SIZE = int(os.environ.get('AGG_BENCH_BATCH', 2000))
# Deterministic: a benchmark whose data changes run to run cannot show a
# regression, only noise.
RANDOM_SEED = int(os.environ.get('AGG_BENCH_SEED', 20260801))

DB_PREFIX = 'agg'
PARTNER_COUNT = 200
PRODUCT_COUNT = 50
MONTHS_OF_HISTORY = 24


def _guard_database(env):
    """Refuse to touch a database this script does not own (R-004)."""
    db_name = env.cr.dbname
    if not db_name.startswith(DB_PREFIX):
        raise SystemExit(
            "REFUSING to seed %r: the aggregation benchmark owns the '%s*' "
            "namespace only. rt*/e2e*/prov*/loadtest* belong to other suites "
            "and seeding them destroys their fixtures (CLAUDE.md, R-004)."
            % (db_name, DB_PREFIX)
        )


def _ensure_partners(env, rng):
    Partner = env['res.partner']
    existing = Partner.search([('ref', 'like', 'AGGBENCH-%')], limit=PARTNER_COUNT)
    if len(existing) >= PARTNER_COUNT:
        return existing
    missing = PARTNER_COUNT - len(existing)
    created = Partner.create([{
        'name': 'AggBench Customer %04d' % index,
        'ref': 'AGGBENCH-P%04d' % index,
        'is_company': True,
        'customer_rank': 1,
    } for index in range(missing)])
    return existing | created


def _ensure_products(env, rng):
    Product = env['product.product']
    existing = Product.search(
        [('default_code', 'like', 'AGGBENCH-%')], limit=PRODUCT_COUNT)
    if len(existing) >= PRODUCT_COUNT:
        return existing
    missing = PRODUCT_COUNT - len(existing)
    created = Product.create([{
        'name': 'AggBench Item %03d' % index,
        'default_code': 'AGGBENCH-I%03d' % index,
        'list_price': round(rng.uniform(50, 5000), 2),
        'type': 'consu',
    } for index in range(missing)])
    return existing | created


def seed(env, target_rows=TARGET_ROWS):
    """Create ``target_rows`` sale.order.line rows spread over real orders.

    ``sale.order.line`` is the volume model on purpose: it is what a revenue or
    top-customer aggregation actually groups over, and it carries the stored
    monetary computes that make a 100k-row read_group non-trivial.
    """
    _guard_database(env)
    rng = random.Random(RANDOM_SEED)

    partners = _ensure_partners(env, rng)
    products = _ensure_products(env, rng)
    _logger.info('AGGBENCH: %d partners, %d products ready',
                 len(partners), len(products))

    Order = env['sale.order']
    existing_lines = env['sale.order.line'].search_count(
        [('order_id.client_order_ref', 'like', 'AGGBENCH-%')])
    if existing_lines >= target_rows:
        _logger.info('AGGBENCH: %d lines already present — nothing to do',
                     existing_lines)
        return existing_lines

    partner_ids = partners.ids
    product_ids = products.ids
    lines_per_order = 5
    remaining = target_rows - existing_lines
    orders_needed = (remaining + lines_per_order - 1) // lines_per_order
    today = date.today()
    started = time.monotonic()
    made = 0

    for batch_start in range(0, orders_needed, BATCH_SIZE // lines_per_order):
        batch = []
        batch_orders = min(BATCH_SIZE // lines_per_order,
                           orders_needed - batch_start)
        for offset in range(batch_orders):
            index = batch_start + offset
            order_date = today - timedelta(
                days=rng.randint(0, MONTHS_OF_HISTORY * 30))
            batch.append({
                'partner_id': rng.choice(partner_ids),
                'client_order_ref': 'AGGBENCH-%06d' % index,
                'date_order': order_date,
                'order_line': [(0, 0, {
                    'product_id': rng.choice(product_ids),
                    'product_uom_qty': rng.randint(1, 20),
                    'price_unit': round(rng.uniform(50, 5000), 2),
                }) for _ in range(lines_per_order)],
            })
        orders = Order.create(batch)
        made += len(orders.order_line)
        env.cr.commit()
        elapsed = time.monotonic() - started
        _logger.info('AGGBENCH: %d/%d lines (%.1fs, %.0f lines/s)',
                     made, remaining, elapsed, made / max(elapsed, 0.001))

    total = env['sale.order.line'].search_count(
        [('order_id.client_order_ref', 'like', 'AGGBENCH-%')])
    _logger.info('AGGBENCH: done — %d sale.order.line rows in %.1fs',
                 total, time.monotonic() - started)
    return total


# ``odoo shell`` execs this file with `env` already bound.
if 'env' in dir():
    seed(env)  # noqa: F821 - provided by odoo shell
