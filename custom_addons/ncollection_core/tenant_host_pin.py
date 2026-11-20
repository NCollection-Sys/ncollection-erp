# -*- coding: utf-8 -*-
"""Server-wide: a VERIFIED custom domain reaches exactly one tenant database.

Platform subdomains route as they always have: ``dbfilter = ^%d$`` takes the first host label (``acme.<base domain>``
-> ``acme``). A custom domain (``erp.customer.com``) cannot route that way - its first label says nothing about the
tenant - so the edge pins the database instead: the nginx block the edge agent generates for a domain that passed
ownership verification sets ``X-NC-Tenant-DB: <database>``.

This module wraps ``odoo.http.db_filter`` (the seam every database choice goes through: the session's database, the
``X-Odoo-Database`` header, the monodb list and the web controllers) so that, when the header is present, the ONLY
database a request can reach is the pinned one - whatever its Host, session cookie or X-Odoo-Database header says.

Trust boundary. The header is honoured only when the server is told to (``NC_TENANT_DB_HEADER=1`` in its
environment) - which a deployment sets only where (a) Odoo listens on loopback behind the edge and (b) every OTHER
edge block clears the header (``proxy_set_header X-NC-Tenant-DB "";`` in the shared proxy snippet), so a client can
never supply it. Without the variable this module changes nothing; the header is ignored. A malformed value pins to
no database at all (fail closed), never to a fallback.
"""
import logging
import os
import re

from odoo import http

_logger = logging.getLogger(__name__)

HEADER = 'X-NC-Tenant-DB'
ENV = 'NC_TENANT_DB_HEADER'
_DB_RE = re.compile(r'^[a-z0-9][a-z0-9_-]{0,62}$')
_NO_DB = object()


def enabled():
    return os.environ.get(ENV) == '1'


def pinned_database():
    """None when nothing is pinned; the pinned name; or _NO_DB when the header is present but invalid."""
    if not enabled():
        return None
    try:
        httprequest = http.request.httprequest
    except (RuntimeError, AttributeError):
        return None  # no request (cron, shell): nothing to pin
    value = httprequest.headers.get(HEADER)
    if value is None:
        return None
    value = value.strip()
    return value if _DB_RE.fullmatch(value) else _NO_DB


def pinned_db_filter(dbs, host=None, _original=None):
    pinned = pinned_database()
    if pinned is None:
        return _original(dbs, host)
    if pinned is _NO_DB:
        return []
    return [db for db in dbs if db == pinned]


def install():
    current = http.db_filter
    if getattr(current, '_nc_host_pin', False):
        return
    original = current

    def db_filter(dbs, host=None):
        return pinned_db_filter(dbs, host, _original=original)

    db_filter._nc_host_pin = True
    db_filter.__wrapped__ = original
    http.db_filter = db_filter
    _logger.info("Tenant host pin installed (seam odoo.http.db_filter; %s %s).", HEADER,
                 "honoured" if enabled() else "ignored: %s is not 1" % ENV)


def installed():
    return bool(getattr(http.db_filter, '_nc_host_pin', False))
