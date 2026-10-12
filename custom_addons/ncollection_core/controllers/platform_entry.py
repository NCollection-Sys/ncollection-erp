# -*- coding: utf-8 -*-
"""Browser endpoints of the platform-administrator entry (tenant side).

``/ncollection/platform/enter#token=...`` (the token is in the URL FRAGMENT; a GET serves a bridge page that POSTs it)
redeems the one-time token the platform pushed to this database
(``ncollection.platform.entry``), signs the browser in as the administrator's passwordless proxy user and lands
on the Summary. ``/ncollection/platform/exit`` ends that session and returns to the platform.

Every failure answers the same bare 403, so the endpoint reveals nothing about which tokens exist. A token is
single-use and lives a few seconds; it is never logged or echoed.
"""
import logging

from odoo import SUPERUSER_ID, http
from odoo.exceptions import AccessDenied
from odoo.http import request

from ..models.platform_entry import CREDENTIAL_TYPE, _URL_RE

_logger = logging.getLogger(__name__)


def _denied():
    return request.make_response('Forbidden', status=403, headers=[('Content-Type', 'text/plain')])


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
            'if(!m){document.body.textContent="Forbidden";return;}'
            'document.getElementById("t").value=m[1];document.getElementById("f").submit();})();</script>'
            '</body></html>')
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
            _logger.info("Platform entry refused from %s: no open grant", request.httprequest.remote_addr)
            return _denied()
        return_url = entry.return_url or ''
        try:
            proxy = Entry._proxy_user(entry)
            request.session.authenticate(request.env, {
                'type': CREDENTIAL_TYPE, 'login': proxy.login, 'token': token})
        except AccessDenied:
            _logger.info("Platform entry refused from %s: grant not redeemable", request.httprequest.remote_addr)
            return _denied()
        # Remember where to return. The session itself is time-boxed (see ResUsers._compute_session_token).
        request.session['nc_platform_return'] = return_url
        response = request.redirect('/odoo', code=303)
        response.headers['Referrer-Policy'] = 'no-referrer'    # the one-time token was in the URL that led here
        return response

    @http.route('/ncollection/platform/exit', type='http', auth='user', methods=['GET'], sitemap=False)
    def leave(self, **_ignored):
        if not request.env.user.nc_platform_proxy:
            return _denied()
        return_url = request.session.get('nc_platform_return') or ''
        request.session.logout(keep_db=True)
        return request.redirect(return_url if _URL_RE.fullmatch(return_url) else '/web/login', local=False, code=303)
