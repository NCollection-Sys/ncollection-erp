# -*- coding: utf-8 -*-
"""Browser endpoints of the platform-administrator entry (tenant side).

``/ncollection/platform/enter#token=...`` (the token is in the URL FRAGMENT; a GET serves a bridge page that POSTs it)
redeems the one-time token the platform pushed to this database
(``ncollection.platform.entry``), signs the browser in as the administrator's passwordless proxy user and lands
on the Summary. ``/ncollection/platform/exit`` ends that session and returns to the platform.

Every failure answers the same bare 403, so the endpoint reveals nothing about which tokens exist. A token is
single-use and lives a few seconds; it is never logged or echoed.
"""
import json
import logging
import threading
import time

from odoo import SUPERUSER_ID, fields, http
from odoo.exceptions import AccessDenied
from odoo.http import request

from ..app_url import app_url
from ..models.platform_entry import CREDENTIAL_TYPE, PROXY_LOGIN_PREFIX, _URL_RE, token_digest

_DOUBLE_SUBMIT_SECONDS = 15

_logger = logging.getLogger(__name__)


_DENIED_PAGE = (
    '<!doctype html><html><head><meta charset="utf-8"><meta name="color-scheme" content="light dark">'
    '<title>Forbidden</title><style>body{font:16px/1.5 system-ui,sans-serif;max-width:34rem;margin:15vh auto;'
    'padding:0 1rem}h1{font-size:1.4rem}</style></head><body><h1>Forbidden</h1>'
    '<p>This entry link is not valid any more: it expires after a minute and works once.</p>'
    '<p>Go back to the platform and press <b>Enter Company</b> again.</p></body></html>')


def _denied(reason='refused'):
    """Every refusal is the same 403 (nothing says which part failed). A browser gets a short page that says what
    to do; anything else gets the bare word. The reason goes to the server log only (never the token)."""
    _logger.info("Platform entry refused from %s: %s", request.httprequest.remote_addr, reason)
    if 'text/html' in (request.httprequest.headers.get('Accept') or ''):
        return request.make_response(_DENIED_PAGE, status=403, headers=[
            ('Content-Type', 'text/html; charset=utf-8'), ('Cache-Control', 'no-store')])
    return request.make_response('Forbidden', status=403, headers=[('Content-Type', 'text/plain')])


def _audit_platform_event(env, event, proxy):
    """Record the entry / exit of a Platform Administrator in the tenant's authentication log, when the tenant has one.

    The proxy signs in by a one-time grant, not by password, so it never passes the login hooks that fill that log;
    without this the log would show every ordinary sign-in and not the one that matters most. The row names the
    PROXY user (a real account whose name is "Platform Admin: <person>"), never a tenant user: it is the actual
    actor, not metadata written to look like one. ``ncollection_auth`` is optional here (a tenant may lack it)."""
    if 'ncollection.auth.log' in env:
        env['ncollection.auth.log'].sudo()._capture(event, user=proxy)


_DENIAL_BUDGET_PER_MINUTE = 30
_denial_counts = {}
_denial_lock = threading.Lock()


def _denial_budget_left(dbname, named=False, now=None):
    """The refusal endpoint is public, and every audited refusal commits a row on its own cursor: without a cap a
    loop of bad tokens fills the log and the connection pool. At most ``_DENIAL_BUDGET_PER_MINUTE`` refusals per
    database and minute are written. Refusals that carry a NAMED proxy login have a budget of their own, so a flood of
    anonymous bad tokens cannot use it up and hide a real one. The caller always gets the same bare 403."""
    window = int((now if now is not None else time.time()) // 60)
    key = (dbname, window, named)
    with _denial_lock:
        for old in [k for k in _denial_counts if k[1] != window]:
            del _denial_counts[old]
        count = _denial_counts[key] = _denial_counts.get(key, 0) + 1
    return count <= _DENIAL_BUDGET_PER_MINUTE


def _audit_platform_denial(login=None, bucket=None):
    """Record a REFUSED entry (an unknown, expired or used link; a grant the proxy could not redeem; a session that is
    not a proxy on the platform exit) in the tenant's authentication log, when it has one.

    It goes through the log's existing failed-login path (``_capture_isolated``: its own cursor, so nothing the refusal
    rolls back can erase it). The row carries the proxy login when one is known, else none; never the token."""
    try:
        env = request.env(user=SUPERUSER_ID, su=True)
        if 'ncollection.auth.log' not in env or not _denial_budget_left(env.cr.dbname, named=bucket or bool(login)):
            return
        env['ncollection.auth.log']._capture_isolated('platform_denied', login=login)
    except Exception:                                   # noqa: BLE001 - the refusal must stay the same bare 403
        _logger.exception("Platform entry refusal could not be written to the authentication log")


class NCollectionPlatformEntry(http.Controller):

    @http.route('/ncollection/platform/enter', type='http', auth='none', methods=['GET'], sitemap=False)
    def enter_page(self, **_ignored):
        """The bridge: moves the token from the URL fragment into a POST body. Looks nothing up, reveals nothing.

        A fragment is never sent to a server, a proxy or an access log, and never appears in a Referer; a query
        string would be all four.
        """
        html = (
            '<!doctype html><html><head><meta charset="utf-8"><meta name="referrer" content="no-referrer">'
            '<title>Platform</title></head><body>'
            '<form id="f" method="post" action="/ncollection/platform/enter">'
            '<input type="hidden" name="token" id="t"/><noscript>JavaScript is required.</noscript></form>'
            '<script>(function(){var m=/^#token=([A-Za-z0-9_-]{1,200})$/.exec(location.hash);'
            'history.replaceState(null,"",location.pathname);'          # off the address bar and out of history
            'if(!m){location.replace(%s);return;}'
            'document.getElementById("t").value=m[1];document.getElementById("f").submit();})();</script>'
            '</body></html>') % json.dumps(app_url() if request.session.uid else '/web/login')
        # No fragment (a reload, a bookmark, a replayed link): a signed-in browser goes on into the app, anyone else
        # to the ordinary login. Nothing is looked up and nothing is granted, so this opens no door.
        return request.make_response(html, headers=[
            ('Content-Type', 'text/html; charset=utf-8'), ('Cache-Control', 'no-store'),
            ('Referrer-Policy', 'no-referrer')])

    # csrf=False: the one-time token IS the credential (a forged cross-site POST cannot know it).
    @http.route('/ncollection/platform/enter', type='http', auth='none', methods=['POST'], readonly=False,
                csrf=False, sitemap=False)
    def enter(self, token=None, **_ignored):
        # auth='none' has no user: do the registry work as the real superuser (a user-less env.sudo() breaks code
        # that reads env.user), and let the session sign in as the PROXY below.
        Entry = request.env(user=SUPERUSER_ID, su=True)['ncollection.platform.entry']
        entry = Entry._lookup_open(token)
        if not entry:
            if self._already_entered_with(Entry, token):
                # the same browser submitted the same link twice (a reload, a double click, a download manager): it
                # is already signed in as this proxy, so go on; nothing new is granted
                return request.redirect(app_url(), code=303)
            _audit_platform_denial()
            return _denied("no open grant")
        return_url = entry.return_url or ''
        try:
            proxy = Entry._proxy_user(entry)
            request.session.authenticate(request.env, {
                'type': CREDENTIAL_TYPE, 'login': proxy.login, 'token': token})
        except AccessDenied:
            _audit_platform_denial(PROXY_LOGIN_PREFIX + entry.platform_ref)     # the proxy login the grant names
            return _denied("grant not redeemable (expired, used, or the proxy refused it)")
        # Remember where to return. The session itself is time-boxed (see ResUsers._compute_session_token).
        request.session['nc_platform_return'] = return_url
        _audit_platform_event(Entry.env, 'platform_entry', proxy)
        response = request.redirect(app_url(), code=303)
        response.headers['Referrer-Policy'] = 'no-referrer'    # the one-time token was in the URL that led here
        return response

    @staticmethod
    def _already_entered_with(Entry, token):
        """True when THIS session is the proxy user that redeemed ``token`` a moment ago (the same POST again)."""
        uid = request.session.uid
        if not uid or not token or len(token) > 200:
            return False
        recent = fields.Datetime.subtract(fields.Datetime.now(), seconds=_DOUBLE_SUBMIT_SECONDS)
        return bool(Entry.search_count([
            ("token_hash", "=", token_digest(token)), ("user_id", "=", uid), ("used_at", ">=", recent)]))

    @http.route('/ncollection/platform/exit', type='http', auth='user', methods=['GET'], sitemap=False)
    def leave(self, **_ignored):
        if not request.env.user.nc_platform_proxy:
            _audit_platform_denial(request.env.user.login, bucket='exit')     # its own budget: signed-in noise
            return _denied()
        return_url = request.session.get('nc_platform_return') or ''
        _audit_platform_event(request.env(user=SUPERUSER_ID, su=True), 'platform_exit', request.env.user)
        request.session.logout(keep_db=True)
        return request.redirect(return_url if _URL_RE.fullmatch(return_url) else '/web/login', local=False, code=303)
