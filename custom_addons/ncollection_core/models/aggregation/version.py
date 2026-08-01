# -*- coding: utf-8 -*-
"""Source-model version counters — the invalidation signal for P4-T01.

ARCHITECTURE_DATA_PLATFORM §9 requires "explicit invalidation on source writes".
Doing that in Odoo 19 without violating Rule 1 (never modify core) or the soft
-dependency rule turns out to be the hard part of this ticket, so the reasoning
is recorded here.

**Why not ``ormcache``?** ``ormcache(cache=...)`` can only name one of the six
groups hardcoded in ``odoo/orm/registry.py`` (``_CACHES_BY_KEY``); a custom
group raises ``KeyError`` in ``Registry.clear_cache`` and has no
``orm_signaling_<name>`` table. Verified live on this build. That leaves
``default`` as the only usable group — and ``default`` is where P1-T10's
``_ncollection_blocked_access_cached`` lives. Invalidating aggregations through
``clear_cache()`` would therefore flush the Ring 2 license cache on every ERP
write: a performance regression on a security-critical path. So the engine
carries its own cache and its own invalidation signal.

**Why a version counter instead of deleting cache entries?** Enumerating and
deleting the entries derived from a model would require an index from model to
cache key, kept correct across workers. A monotonic counter needs neither: the
version is part of the cache key, so bumping it makes every derived entry
unreachable at once, in every worker, with a single integer write. Orphaned
entries age out of the LRU.

**Why ``_inherit = 'base'``?** It is the only hook that sees writes to models
this addon does not (and must not) depend on. P1-T10 already uses exactly this
mechanism for Ring 2 enforcement (``license_enforcement.py``), so it is proven
on this build rather than novel.

**Bounded scope, deliberately.** Only the models in ``AGGREGATION_SOURCE_MODELS``
bump a counter. A declared constant is cross-worker consistent by construction
(no runtime discovery to synchronise) and keeps the write hook to one frozenset
membership test. Aggregating a model outside this set is supported, but its
cached result is bounded by TTL instead of invalidated on write — see
``docs/markdown/AGGREGATION_API.md``. Add a model here when a dashboard needs
write-accurate aggregation over it.
"""

import itertools
import logging
import threading

from odoo import api, fields, models
from odoo.tools import sql

_logger = logging.getLogger(__name__)

# Process-local write counter, merged into the cache key alongside the DB one.
#
# The DB counter alone is not sufficient, and #55 (the engine's first external
# consumer) is what exposed it: a counter row is transactional, so a ROLLBACK
# un-bumps it. Under TransactionCase — which rolls back after every test — the
# next test issues the same spec, computes the same cache key, and is served the
# PREVIOUS test's numbers. Two of #55's fixture tests failed exactly that way,
# each returning its predecessor's answer.
#
# This counter never rolls back, so a key can never be reused after a write in
# this process. Cost is over-invalidation when a transaction aborts (an entry
# becomes unreachable although its data never changed) — colder, never wrong,
# which is the correct direction for a cache. The DB counter still carries
# invalidation ACROSS workers; this one closes the same-process gap.
_process_versions = {}
_process_seq = itertools.count(1)
_process_lock = threading.Lock()

# Source models whose writes invalidate cached aggregations. Scoped to the four
# domains P4-T01 names (sale, account, stock, hr) plus the cross-domain models
# the P1-T17 dashboard already aggregates. Every entry costs one frozenset
# lookup per write to that model, so this stays a curated list, not "everything".
AGGREGATION_SOURCE_MODELS = frozenset({
    # sale
    'sale.order', 'sale.order.line',
    # account (aggregation only — ADR #15 keeps computation in
    # ncollection_account_reports / _analytics, #111 / #120)
    'account.move', 'account.move.line', 'account.payment',
    # stock
    'stock.move', 'stock.move.line', 'stock.picking', 'stock.quant',
    # hr
    'hr.employee', 'hr.leave', 'hr.attendance',
    # cross-domain, already aggregated by the P1-T17 dashboard
    'crm.lead', 'purchase.order', 'purchase.order.line',
    'mail.activity', 'res.partner', 'product.product', 'product.template',
})


class NCollectionAggregationVersion(models.Model):
    """One row per tracked source model, holding a monotonic write counter.

    Read constantly and written on every source write, so it is deliberately
    the smallest possible table and is touched with raw SQL rather than the ORM
    (an ORM write here would recurse straight back into the ``base`` hook).
    """

    _name = 'ncollection.aggregation.version'
    _description = 'NCollection Aggregation Source Version'
    _rec_name = 'model_name'

    model_name = fields.Char(required=True, index=True)
    version = fields.Integer(default=0, required=True)

    # Odoo 19 silently ignores the old `_sql_constraints = [...]` list — use
    # models.Constraint or the index never exists. This one is load-bearing:
    # `_bump` relies on ON CONFLICT (model_name), which Postgres rejects
    # outright without a matching unique constraint.
    _model_name_uniq = models.Constraint(
        'unique(model_name)',
        'One version row per source model.',
    )

    # ------------------------------------------------------------------
    # Bump / read
    # ------------------------------------------------------------------

    @api.model
    def _bump_process(self, model_name):
        """Advance this model's PROCESS-local counter. Never rolls back.

        Deliberately outside the transaction: that is the whole point. See the
        module header for why the DB counter alone lets a rolled-back test serve
        its predecessor's cached numbers.
        """
        with _process_lock:
            _process_versions[model_name] = next(_process_seq)

    @api.model
    def _merge_process_versions(self, db_versions):
        """Combine the DB counter with the process one, per model.

        Lock ordering note: ``cache.py``'s ``_lock`` may be held while calling
        this, which then takes ``_process_lock``. That direction is the only one
        used anywhere — nothing in this module ever reaches back into the cache
        module's lock — so there is no cycle. Keep it that way.

        The value becomes a ``(db, process)`` pair rather than an int. Both
        halves matter and neither subsumes the other: the DB half invalidates
        across workers, the process half cannot be undone by a rollback. A
        model that has only ever been written in this process still gets an
        entry, which is what makes a test's first write invalidate correctly.
        """
        with _process_lock:
            names = set(db_versions) | set(_process_versions)
            return {
                name: (db_versions.get(name, 0), _process_versions.get(name, 0))
                for name in names
            }

    @api.model
    def _bump(self, model_name):
        """Increment ``model_name``'s counter. Never raises into a write.

        Raw SQL on purpose: this runs inside every tracked create/write/unlink,
        so ORM overhead here is overhead on the whole ERP. The UPSERT is atomic,
        which matters because concurrent writers to the same model race here by
        definition.

        The SAVEPOINT is not optional. A failed statement aborts the whole
        Postgres transaction, and catching the Python exception does NOT undo
        that — every later query in the request then dies with
        ``InFailedSqlTransaction``. Without the savepoint this "fail-open"
        handler would convert a bump failure into a broken request, which is
        the exact opposite of its intent. (Found the hard way: the first run of
        these tests cascaded four unrelated failures from one bad statement.)

        ``flush=False`` is equally load-bearing. The default ``savepoint()``
        returns a ``_FlushingSavepoint``, which calls ``cr.flush()`` BEFORE
        issuing SAVEPOINT and again on a clean exit. Two costs, both real:
        this method runs inside every create/write/unlink on the hottest models
        in the ERP, so a full ORM flush of the entire pending transaction here
        would defeat Odoo's own write batching — while the docstring above
        claims the opposite. And the pre-SAVEPOINT flush happens OUTSIDE the
        savepoint, so if that flush raises (an unrelated dirty field failing a
        constraint) the transaction is poisoned with no savepoint to roll back
        to — reopening the exact hole this is here to close. Neither method
        touches ORM-cached field state, so the flush buys nothing.
        """
        self._bump_process(model_name)
        try:
            with self.env.cr.savepoint(flush=False):
                self.env.cr.execute("""
                INSERT INTO ncollection_aggregation_version
                            (model_name, version, create_uid, create_date,
                             write_uid, write_date)
                     VALUES (%s, 1, %s, now() AT TIME ZONE 'UTC',
                             %s, now() AT TIME ZONE 'UTC')
                    ON CONFLICT (model_name)
                      DO UPDATE SET version = ncollection_aggregation_version.version + 1,
                                    write_date = now() AT TIME ZONE 'UTC'
                """, (
                    model_name,
                    # uid falls back to 1 (__system__) only where env.uid is
                    # unset (early boot / raw cursor). The counter's VALUE is
                    # what matters here; create_uid is audit garnish.
                    self.env.uid or 1,
                    self.env.uid or 1,
                ))
        except Exception:  # pragma: no cover - defensive: never break a write
            _logger.exception(
                'Aggregation version bump failed for %s — cached aggregations '
                'may serve stale data until their TTL expires (fail-open).',
                model_name)

    @api.model
    def _versions(self):
        """``{model_name: version}`` for every tracked model with a row.

        One indexed scan of a table with at most ``len(AGGREGATION_SOURCE_MODELS)``
        rows. Cheap enough to call per aggregation, and the cache layer wraps it
        in a short TTL anyway.

        Savepointed (and non-flushing) for the same reasons as ``_bump``: a
        failing SELECT poisons the transaction just as thoroughly as a failing
        INSERT, and a flushing savepoint would both cost a full ORM flush and
        leave the pre-SAVEPOINT flush outside the protected region.
        """
        try:
            with self.env.cr.savepoint(flush=False):
                self.env.cr.execute(
                    "SELECT model_name, version "
                    "FROM ncollection_aggregation_version")
                return dict(self.env.cr.fetchall())
        except Exception:  # pragma: no cover - defensive fail-open
            _logger.exception(
                'Aggregation version read failed — treating every cached entry '
                'as stale (fail-open).')
            return {}


class AggregationInvalidationHook(models.AbstractModel):
    """Bumps a source model's version on write. Universal by necessity.

    ``_inherit = 'base'`` merges this into every model in the registry, which is
    the only way to observe writes to ``sale.order`` from an addon that must not
    depend on ``sale``. The cost paid on EVERY write in the tenant is one
    frozenset membership test, so the guard clause is kept first and cheap.
    """

    _inherit = 'base'

    def _nc_bump_aggregation_version(self):
        if self._name not in AGGREGATION_SOURCE_MODELS:
            return
        # `_inherit = 'base'` takes effect the moment these classes load, which
        # is BEFORE _auto_init() creates our table on a fresh database. Tracked
        # models get written during provisioning (`odoo -i base,sale,account…`,
        # demo data, other modules' installs) inside that window. The savepoint
        # would absorb the UndefinedTable safely, but _bump's fail-open handler
        # logs a full traceback each time — hundreds of them on a demo-data
        # install, which is exactly how a real error gets buried. Skip cheaply
        # until the table exists.
        if not sql.table_exists(self.env.cr, 'ncollection_aggregation_version'):
            return
        self.env['ncollection.aggregation.version']._bump(self._name)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._nc_bump_aggregation_version()
        return records

    def write(self, vals):
        result = super().write(vals)
        # `record.write({})` is a real pattern in Odoo core (triggering recompute
        # chains), and it changes no data — bumping would spend a savepoint and
        # an UPSERT to invalidate caches that are still perfectly valid.
        if vals:
            self._nc_bump_aggregation_version()
        return result

    def unlink(self):
        # Bump BEFORE the rows disappear: after super() the recordset is empty,
        # but self._name is still available either way — ordering here is about
        # not losing the signal if super() raises.
        self._nc_bump_aggregation_version()
        return super().unlink()
