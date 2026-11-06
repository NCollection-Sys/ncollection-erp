# -*- coding: utf-8 -*-
"""Admission control for AI requests (P5-T06 / #63): a per-database cap AND a server-wide cap.

An AI request holds ONE web worker for the provider's whole latency, whether it is answered in one piece or streamed.
Measured on a ``--workers=2`` server with a 7.7 s answer: two concurrent questions made an ordinary request wait 6.2 s.
Odoo's workers are shared by every database on the server, so two limits apply:

* each database may have at most N requests in flight (one tenant cannot take the pool), and
* all databases together may have at most M in flight, where M is derived from Odoo's ``workers`` setting and is never
  more than ``workers - 1`` (several tenants each using their own allowance cannot take the pool either).

A refused request is told so at once with a friendly message; the only waiting allowed is a fraction of a second
(``AiAdmission``), because a waiting request holds a worker too.

Both limits are Postgres ADVISORY LOCKS, one per slot, so they hold across worker processes, which a Python counter would
not. The per-database lock lives in the tenant's own database (advisory locks are database-scoped, exactly the scope
wanted); the server-wide lock lives in the maintenance database, the one place every tenant's request can see in common.

WHY THIS IS NOT IN models/. ``tests/test_isolation.py`` forbids ``cursor()`` / ``registry(`` in every model file,
because a model must read only through ``self.env`` (one database). These helpers need cursors of their OWN, since a
streamed answer outlives the request's cursor, but they never touch data: the tenant slot uses only the registry its
caller hands it (``env.registry`` = the request's database); the server slot opens only the fixed maintenance database and
executes only advisory-lock calls. Tests pin both.
"""
import time

_LOCK_CLASS = 0x4E434149  # 'NCAI'
_GLOBAL_LOCK_CLASS = 0x4E43474C  # 'NCGL'
_MAINTENANCE_DB = 'postgres'


class AiSlot:
    """One of N per-database AI request slots, held until ``release()``.

    Opens its own cursor and keeps a session-level advisory lock on it. Always release in a ``finally``: the
    connection returns to the pool, so the lock must be dropped explicitly (a leaked lock would shrink the tenant's
    capacity until the pooled connection happens to be closed).
    """

    def __init__(self, registry, limit):
        self._registry = registry
        self._limit = max(1, limit)
        self._cr = None
        self._slot = None

    def _lock_class(self):
        return _LOCK_CLASS

    def _cursor(self):
        return self._registry.cursor()

    def _try(self, cr, index):
        cr.execute("SELECT pg_try_advisory_lock(%s, %s)", (self._lock_class(), index))
        return bool(cr.fetchone()[0])

    def acquire(self):
        cr = self._cursor()
        try:
            for index in range(self._limit):
                if self._try(cr, index):
                    self._cr, self._slot = cr, index
                    return True
        except Exception:
            cr.close()
            raise
        cr.close()
        return False

    def release(self):
        cr, self._cr = self._cr, None
        if cr is None:
            return
        try:
            cr.execute("SELECT pg_advisory_unlock(%s, %s)", (self._lock_class(), self._slot))
            # Nothing was written, so this changes nothing in production; it releases the savepoint a test cursor
            # holds (closing without it would roll back work done on the shared test connection meanwhile).
            cr.commit()  # pylint: disable=invalid-commit
        finally:
            cr.close()


def global_limit(workers, configured=None):
    """How many AI requests may be in flight across ALL tenants, given Odoo's ``workers`` setting.

    Pre-forked (``workers`` > 0): never more than ``workers - 1``, so at least one worker always serves everything else;
    the default is half of them (at least 1). Threaded mode (``workers`` = 0) has no fixed pool: default 2.
    """
    workers = int(workers or 0)
    if workers > 0:
        wanted = max(1, workers // 2) if configured is None else max(1, int(configured))
        return min(wanted, max(1, workers - 1))
    return 2 if configured is None else max(1, int(configured))


class GlobalAiSlot(AiSlot):
    """A server-wide slot: the same advisory-lock mechanism, held in the maintenance database."""

    def __init__(self, limit):
        super().__init__(None, limit)

    def _lock_class(self):
        return _GLOBAL_LOCK_CLASS

    def _cursor(self):
        from odoo.sql_db import db_connect  # pylint: disable=import-outside-toplevel
        return db_connect(_MAINTENANCE_DB).cursor()


class AiAdmission:
    """Both slots (server-wide, then this database's) with a short bounded wait, released together."""

    _POLL_SECONDS = 0.05

    def __init__(self, registry, tenant_limit, server_limit, wait_seconds=0.0):
        self._tenant = AiSlot(registry, tenant_limit)
        self._server = GlobalAiSlot(server_limit)
        self._wait = max(0.0, float(wait_seconds))
        self._held = []

    def _attempt(self):
        if not self._server.acquire():
            return False
        try:
            taken = self._tenant.acquire()
        except Exception:
            self._server.release()
            raise
        if not taken:
            self._server.release()
            return False
        self._held = [self._tenant, self._server]
        return True

    def acquire(self):
        deadline = time.monotonic() + self._wait
        while True:
            if self._attempt():
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(self._POLL_SECONDS)

    def release(self):
        """Last in, first out (savepoint-based test cursors require it; harmless for independent production cursors)."""
        held, self._held = self._held, []
        for slot in held:
            slot.release()
