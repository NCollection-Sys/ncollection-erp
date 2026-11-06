# -*- coding: utf-8 -*-
"""Per-database cap on AI requests in flight (P5-T06 / #63).

An AI request holds ONE web worker for the provider's whole latency, whether it is answered in one piece or streamed.
Measured on a ``--workers=2`` server with a 7.7 s answer: two concurrent questions made an ordinary request wait 6.2 s,
and the workers are shared by every tenant. So each database may have at most N AI requests in flight; the next is
refused at once with a friendly message instead of queueing behind the provider.

The limit is a Postgres ADVISORY LOCK per slot. Advisory locks are scoped to the current DATABASE, which is exactly the
per-tenant scope wanted, and they hold across worker processes, which a Python counter would not.

WHY THIS IS NOT IN models/. ``tests/test_isolation.py`` forbids ``cursor()`` / ``registry(`` in every model file,
because a model must read only through ``self.env`` (one database). This helper needs a cursor of its OWN, since a
streamed answer outlives the request's cursor, but it never touches data and it only ever uses the registry its caller hands it
(``env.registry`` = the request's database). It builds no Registry, opens no connection by name, and reads no table;
a test pins that.
"""

_LOCK_CLASS = 0x4E434149  # 'NCAI'


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

    def _try(self, cr, index):
        cr.execute("SELECT pg_try_advisory_lock(%s, %s)", (_LOCK_CLASS, index))
        return bool(cr.fetchone()[0])

    def acquire(self):
        cr = self._registry.cursor()
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
            cr.execute("SELECT pg_advisory_unlock(%s, %s)", (_LOCK_CLASS, self._slot))
            # Nothing was written, so this changes nothing in production; it releases the savepoint a test cursor
            # holds (closing without it would roll back work done on the shared test connection meanwhile).
            cr.commit()  # pylint: disable=invalid-commit
        finally:
            cr.close()
