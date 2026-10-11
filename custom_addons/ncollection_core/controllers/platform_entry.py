# -*- coding: utf-8 -*-
"""Browser endpoints of the platform-administrator entry (tenant side).

``/ncollection/platform/enter?token=...``  redeems the one-time token the platform pushed to this database
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

    @http.route('/ncollection/platform/enter', type='http', auth='none', methods=['GET'], readonly=False,
                sitemap=False)
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
