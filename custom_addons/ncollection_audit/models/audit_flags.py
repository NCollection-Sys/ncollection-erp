# -*- coding: utf-8 -*-
"""Server-side switches of the audit capture, deliberately NOT context keys.

A context key is client-controlled: ``/web/dataset/call_kw`` applies whatever context the browser sends, so a key
that switched recording off (or labelled a write as an Undo) would let any user write without a trace or forge an
Undo's attribution (security review F1 / F2). These flags live in a thread-local that only server code sets, for the
duration of a ``with`` block, and are always restored.
"""
import contextlib
import threading

_STATE = threading.local()


def capture_suppressed():
    return getattr(_STATE, 'suppress', 0) > 0


def current_undo_id():
    return getattr(_STATE, 'undo_id', False) or False


@contextlib.contextmanager
def suppress_capture():
    """Used ONLY by the Undo dependency probes, whose effects are rolled back anyway."""
    _STATE.suppress = getattr(_STATE, 'suppress', 0) + 1
    try:
        yield
    finally:
        _STATE.suppress -= 1


@contextlib.contextmanager
def undo_scope(undo_id):
    """Audit rows written inside the block belong to this Undo."""
    previous = getattr(_STATE, 'undo_id', False)
    _STATE.undo_id = undo_id
    try:
        yield
    finally:
        _STATE.undo_id = previous
