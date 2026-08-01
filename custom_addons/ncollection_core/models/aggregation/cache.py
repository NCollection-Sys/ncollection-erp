# -*- coding: utf-8 -*-
"""Two-tier aggregation cache (P4-T01).

Tier 1 — a per-process dict. The dict lookup itself is sub-microsecond, but note
what a caller actually pays on a hit: ``engine.aggregate()`` runs a live
``_model_readable()`` licensing probe (~0.2ms) BEFORE consulting the cache, on
every call, so a warm call costs roughly that probe rather than the dict. That
ordering is deliberate and must not be "optimised" away — it is what makes a plan
downgrade take effect synchronously, since a licence change is not a source-model
write and therefore bumps no version counter. Reordering for speed would reopen
the stale-cache licence bypass. The pattern is otherwise lifted from
``ncollection_subscription/models/dashboard.py`` (module-level dict, monotonic
clock, success-only caching), which already carries an expensive infra read in
production.

Tier 0 — the source-model version counters in ``version.py``, which are part of
every cache key. A write to ``sale.order`` bumps its counter, so every entry
derived from ``sale.order`` becomes unreachable in every worker at once. This is
what makes a per-process dict safe across workers: entries are never *wrong*,
only ever *unreachable*, and unreachable entries age out of the LRU.

**The security-critical part of the key.** ``uid`` AND a context fingerprint are
both in the cache key. Odoo record rules and P1-T10 Ring 2 make an aggregate
user-dependent: the same spec legitimately yields different numbers for
different users, and serving one user's figure to another is a data leak, not a
stale-cache annoyance. Keying by the user's *group set* would give a better hit
rate, but record rules can be per-user (``user_id = uid`` domains are routine),
so groups are not a sufficient proxy.

``uid`` alone is **not** sufficient either. Multi-company security is row-level
through ``ir.rule`` domains reading ``allowed_company_ids``, so one user
switching active company keeps the same uid while their legitimate result
changes completely — the first version of this file keyed without it and would
have served Company A's receivables on Company B's dashboard for up to the TTL.
See ``context_fingerprint``; ``test_cache_is_keyed_by_company`` locks it in.

**Bounded size.** The dict is capped and evicts oldest-first. An aggregation
cache with no ceiling is a memory leak with extra steps — dashboards generate
new keys on every date-range change.

**Staleness bound for untracked models.** A spec over a model outside
``AGGREGATION_SOURCE_MODELS`` gets no write signal, so its entries expire on TTL
alone. That is a documented limitation, not an oversight — see
``docs/markdown/AGGREGATION_API.md``.
"""

import hashlib
import json
import logging
import threading
import time

_logger = logging.getLogger(__name__)

# Entries expire on TTL even when no source write happens. This bounds staleness
# for untracked models and puts a ceiling on how long a missed bump can matter.
DEFAULT_TTL = 300.0  # seconds

# The version snapshot is re-read from the DB at most this often. Without it,
# every cache hit would still cost a query, which defeats the cache; with it,
# a write is visible to other workers within this window. Kept short because it
# is the one window in which a bumped counter is not yet honoured.
VERSION_TTL = 1.0  # seconds

# Hard ceiling on entries. Chosen so a tenant churning date ranges cannot grow
# the process heap without bound; eviction is oldest-first.
MAX_ENTRIES = 512

# {cache_key: (stored_at, value)} — process-local by design (see module docstring).
_cache = {}
# {db_name: (read_at, {model_name: version})}
_version_snapshots = {}
# Guards both dicts. Odoo workers are threaded, so two requests can race here.
_lock = threading.RLock()


def _digest(payload):
    """Stable short hash of an arbitrary JSON-able payload.

    ``sort_keys`` matters: two specs that differ only in dict ordering must
    produce the same key or the cache silently never hits. ``default=str``
    keeps a stray date or Decimal from raising instead of caching.
    """
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode('utf-8')).hexdigest()[:32]


def versions_for(env, ttl=VERSION_TTL):
    """Version snapshot for this database, re-read at most every ``ttl``."""
    db_name = env.cr.dbname
    now = time.monotonic()
    with _lock:
        cached = _version_snapshots.get(db_name)
        if cached and (now - cached[0]) < ttl:
            return cached[1]
    # Read outside the lock: this touches the DB and must not serialise workers.
    versions = env['ncollection.aggregation.version'].sudo()._versions()
    with _lock:
        _version_snapshots[db_name] = (now, versions)
    return versions


def context_fingerprint(env):
    """The parts of the context that change what ``_read_group`` returns.

    Keying on ``uid`` alone is NOT enough, and the gap is a silent wrong-number
    bug rather than a stale-data one:

    * ``allowed_company_ids`` — Odoo's multi-company security is row-level via
      ``ir.rule`` domains like ``[('company_id', 'in', company_ids)]``, where
      ``company_ids`` comes from ``env.companies`` (itself derived from this
      context key). A user who belongs to two companies and flips the company
      switcher keeps the same ``uid``, so without this the SAME key would serve
      Company A's receivables on Company B's dashboard. Every model in
      ``AGGREGATION_SOURCE_MODELS`` is company-scoped this way.
    * ``active_test`` — decides whether archived rows are counted.
    * ``tz`` — changes bucket boundaries for ``groupby: ['date:month']``.
    * ``lang`` — changes the ``display_name`` that ``_flatten_cell`` bakes into
      a cached row.

    Projected explicitly rather than hashing the whole context: the full context
    carries per-request noise (``params``, ``bin_size``) that would shred the
    hit rate without affecting the result.
    """
    context = env.context
    companies = context.get('allowed_company_ids')
    if not companies:
        # env.company is the single active company when no explicit list is set.
        companies = [env.company.id] if env.company else []
    return {
        'companies': tuple(sorted(companies)),
        'lang': context.get('lang'),
        'tz': context.get('tz'),
        'active_test': context.get('active_test', True),
    }


def make_key(env, spec, versions):
    """Cache key for ``spec`` as seen by the current user, in this context.

    Includes, in order: database (never share across tenants), user (record
    rules and Ring 2 make results user-dependent), superuser flag, the context
    fingerprint above, the spec itself, and the version of the spec's source
    model (the invalidation signal).
    """
    model_name = spec.get('model')
    return _digest({
        'db': env.cr.dbname,
        'uid': env.uid,
        'su': bool(env.su),
        'ctx': context_fingerprint(env),
        'spec': spec,
        'version': versions.get(model_name, 0),
    })


def get(key):
    """Cached value, or None when absent or expired.

    Returns a COPY. Handing back the stored list would let one caller's
    ``rows.sort()`` or ``rows.append(total_row)`` silently rewrite the entry for
    every other request on that key until the TTL expires — cross-request data
    corruption with no exception and no log, which is the one failure class this
    module exists to prevent. The dashboards in #55/#56/#57 are exactly the
    consumers likely to sort or annotate rows for display.
    """
    now = time.monotonic()
    with _lock:
        entry = _cache.get(key)
        if not entry:
            return None
        stored_at, value = entry
        if (now - stored_at) >= DEFAULT_TTL:
            _cache.pop(key, None)
            return None
        return list(value)


def put(key, value):
    """Store a value. Success-only — callers never cache a failure."""
    now = time.monotonic()
    with _lock:
        if len(_cache) >= MAX_ENTRIES and key not in _cache:
            # Oldest-first eviction. A full sort is fine at this ceiling and
            # keeps the structure a plain dict (no extra dependency).
            for old_key, _entry in sorted(
                    _cache.items(), key=lambda item: item[1][0]
            )[:max(1, MAX_ENTRIES // 8)]:
                _cache.pop(old_key, None)
        _cache[key] = (now, value)


def clear():
    """Drop every cached entry in THIS process. Used by tests and manual flush.

    Deliberately takes no ``db_name``: entries are keyed by an opaque digest, so
    a per-tenant purge would require tracking key provenance. An earlier draft
    accepted ``db_name`` and ignored it for entries while honouring it for
    snapshots — a footgun for anyone later building a per-tenant "flush my
    cache" action on that signature, since it would silently cold every other
    tenant sharing the worker. Global-only is over-invalidation: never wrong,
    only colder.
    """
    with _lock:
        _cache.clear()
        _version_snapshots.clear()


def stats():
    """Introspection for the benchmark harness and for debugging."""
    with _lock:
        return {
            'entries': len(_cache),
            'max_entries': MAX_ENTRIES,
            'ttl': DEFAULT_TTL,
            'version_ttl': VERSION_TTL,
            'databases_tracked': len(_version_snapshots),
        }
