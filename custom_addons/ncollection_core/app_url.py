# -*- coding: utf-8 -*-
"""The ONE public application URL namespace of NCollection: ``/atlas/...`` (the web client), never ``/odoo/...``.

THE CONTRACT
------------
* ``/atlas/...`` (``APP_PATH``) is the public application UI namespace: what the address bar shows for every screen,
  deep link, reload and back / forward.
* ``/web/...`` stays Odoo's technical namespace (``/web/login``, ``/web/image``, ``/web/content``, ``/web/assets``,
  ``/web/dataset``, ``/web/action``, ...). It is NOT renamed: those are API endpoints, not pages.
* ``/odoo/...`` keeps WORKING (every link Odoo itself generates - e-mails, chatter mentions, ``_get_access_action``,
  its own server redirects, other modules' ``startUrl()`` - still points there) but never stays in the address bar:
  the server answers it with a redirect to the same path under ``APP_PATH``, query string kept.

ONE VALUE, ONE BUILDER
----------------------
``APP_PATH`` comes from the environment variable ``NC_APP_PATH`` (``/atlas`` when unset; the same default in
development and production), read once per process - Odoo registers routes at import time, so it cannot come from
a database. The web client receives it in ``session_info['nc_app_path']``. Every NCollection URL into the
application is built by ``app_url()`` (or mapped by ``to_app_url()``); nothing else spells the prefix. The host is
never part of it: it stays whatever the request / ``web.base.url`` / the configured base domain says.

WHY THIS IS SAFE ACROSS ODOO UPGRADES
-------------------------------------
No Odoo file is changed. Server side: one controller (``controllers/app_routes.py``) adds the ``APP_PATH`` routes to
Odoo's own ``Home.web_client`` (same method, same checks) and maps the ``/odoo`` redirects. Client side:
``static/src/router/app_path.js`` patches ``router.stateToUrl`` / ``router.urlToState``, the two conversions Odoo's
``router`` object exports for exactly this ("state <-> url conversions can be patched if needed in a custom
webclient"), re-runs the exported ``startRouter()`` and adds the one listener Odoo's private click handler cannot
cover.
``app_url_seam_problem()`` / the JS test fail loudly if an Odoo update moves any of it.
"""
import logging
import os
import re
from urllib.parse import urlsplit, urlunsplit

_logger = logging.getLogger(__name__)

APP_PATH_ENV = 'NC_APP_PATH'
DEFAULT_APP_PATH = '/atlas'
ODOO_PATH = '/odoo'
_VALID = re.compile(r'/[a-z][a-z0-9_-]{1,30}')
# Odoo / NCollection namespaces the application path may never shadow.
_RESERVED = frozenset({
    '/odoo', '/web', '/scoped_app', '/json', '/jsonrpc', '/xmlrpc', '/api', '/report', '/mail', '/bus', '/websocket',
    '/longpolling', '/my', '/portal', '/website', '/shop', '/ncollection', '/queue_job', '/auth_oauth', '/login',
    '/signup', '/reset', '/static', '/favicon.ico', '/robots.txt', '/health',
})


def _read_app_path():
    value = (os.environ.get(APP_PATH_ENV) or DEFAULT_APP_PATH).strip().rstrip('/')
    if not _VALID.fullmatch(value) or value in _RESERVED:
        _logger.critical("%s=%r is not a valid application path (a lowercase /segment that is not an Odoo or "
                         "NCollection namespace); using %s", APP_PATH_ENV, value, DEFAULT_APP_PATH)
        return DEFAULT_APP_PATH
    return value


APP_PATH = _read_app_path()


def app_url(path=''):
    """``/atlas`` + ``path`` (``'action-12/new'``, ``'/res.partner/5'``, ``'?debug=1'`` or nothing)."""
    path = (path or '').lstrip('/')
    if not path:
        return APP_PATH
    return APP_PATH + ('' if path.startswith('?') else '/') + path


def to_app_url(url):
    """``url`` with a leading ``/odoo`` application path mapped to ``APP_PATH`` (relative or absolute; query and
    fragment kept). Anything that is not an ``/odoo`` application URL is returned unchanged."""
    if not url:
        return url
    parts = urlsplit(url)
    path = parts.path
    if path == ODOO_PATH or path.startswith(ODOO_PATH + '/'):
        return urlunsplit((parts.scheme, parts.netloc, APP_PATH + path[len(ODOO_PATH):], parts.query,
                           parts.fragment))
    return url


def is_app_path(path):
    """True for the web client's own HTML entry points (/atlas, /atlas/..., /odoo, /odoo/..., /web)."""
    return (path in (APP_PATH, ODOO_PATH, '/web') or path.startswith(APP_PATH + '/')
            or path.startswith(ODOO_PATH + '/'))
