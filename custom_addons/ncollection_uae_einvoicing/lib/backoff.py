# -*- coding: utf-8 -*-
"""Retry timing for ASP submissions and polls. Pure: no Odoo, no clock of its own.

Exponential backoff with a cap and a DETERMINISTIC jitter: the jitter is derived from the idempotency key and the
attempt number, so two workers that compute the delay of the same attempt agree, and a test can assert the exact
value. A provider's ``Retry-After`` is a floor, never ignored and never shortened. After ``MAX_ATTEMPTS`` transient
failures the submission is final (dead letter): an administrator decides, nothing retries on a guess."""

import hashlib

BASE_SECONDS = 60
CAP_SECONDS = 6 * 3600
MAX_ATTEMPTS = 8
JITTER_FRACTION = 0.2  # +/- 20 %


def _jitter(key, attempt):
    digest = hashlib.sha256(('%s|%d' % (key, attempt)).encode('utf-8')).digest()
    unit = int.from_bytes(digest[:4], 'big') / 0xFFFFFFFF  # 0..1
    return (unit * 2 - 1) * JITTER_FRACTION  # -0.2..+0.2


def delay(attempt, key='', retry_after=None):
    """Seconds to wait before attempt ``attempt + 1``, after ``attempt`` failed (attempt >= 1)."""
    if attempt < 1:
        raise ValueError('attempt starts at 1')
    raw = min(CAP_SECONDS, BASE_SECONDS * (2 ** (attempt - 1)))
    seconds = int(round(raw * (1 + _jitter(key, attempt))))
    seconds = max(1, min(CAP_SECONDS, seconds))
    if retry_after:
        seconds = max(seconds, min(int(retry_after), CAP_SECONDS))
    return seconds


def exhausted(attempt):
    """True once ``attempt`` transient failures leave no retry."""
    return attempt >= MAX_ATTEMPTS
