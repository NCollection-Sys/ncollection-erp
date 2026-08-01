# -*- coding: utf-8 -*-
"""Two-tier aggregation cache (P4-T01).

Tier 1 — a per-process dict, microsecond reads. The pattern is lifted from
``ncollection_subscription/models/dashboard.py`` (module-level dict, monotonic
clock, success-only caching), which already carries an expensive infra read in
production.

Tier 0 — the source-model version counters in ``version.py``, which are part of
every cache key. A write to ``sale.order`` bumps its counter, so every entry
derived from ``sale.order`` becomes unreachable in every worker at once. This is
what makes a per-process dict safe across workers: entries are never *wrong*,
only ever *unreachable*, and unreachable entries age out of the LRU.

**The security-critical part of the key.** ``uid`` is in the cache key. Odoo
record rules and P1-T10 Ring 2 both make an aggregate user-dependent: the same
spec legitimately yields different numbers for different users, and serving one
user's figure to another is a data leak, not a stale-cache annoyance. Keying by
the user's *group set* would give a better hit rate, but record rules can be
per-user (``user_id = uid`` domains are routine), so groups are not a sufficient
proxy. Per-uid is the deliberately conservative choice; ``test_cache_is_keyed_by_user``
locks it in.

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


def make_key(env, spec, versions):
    """Cache key for ``spec`` as seen by the current user.

    Includes, in order: database (never share across tenants), user (record
    rules and Ring 2 make results user-dependent), the spec itself, and the
    version of the spec's source model (the invalidation signal).
    """
    model_name = spec.get('model')
    return _digest({
        'db': env.cr.dbname,
        'uid': env.uid,
        'su': bool(env.su),
        'spec': spec,
        'version': versions.get(model_name, 0),
    })


def get(key):
    """Cached value, or None when absent or expired."""
    now = time.monotonic()
    with _lock:
        entry = _cache.get(key)
        if not entry:
            return None
        stored_at, value = entry
        if (now - stored_at) >= DEFAULT_TTL:
            _cache.pop(key, None)
            return None
        return value


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


def clear(db_name=None):
    """Drop cached entries. Used by tests and by an explicit flush.

    ``db_name`` is accepted for symmetry with the version snapshots; entries are
    keyed by an opaque digest that already includes the database, so a targeted
    purge would mean tracking key provenance. Clearing everything in this process
    is correct (never wrong, only colder) and keeps the structure simple.
    """
    with _lock:
        _cache.clear()
        if db_name is None:
            _version_snapshots.clear()
        else:
            _version_snapshots.pop(db_name, None)


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
