# -*- coding: utf-8 -*-
"""The server half of the public application path (``app_url.py``): ``/atlas/...`` serves the web client, ``/odoo/...``
redirects there, and every redirect Odoo's ``Home`` controller makes into the application lands on ``/atlas``.

Extends Odoo's own ``Home`` (no copy of its logic): ``web_client`` gets the ``APP_PATH`` routes next to its own and
runs unchanged for them (session, login redirect with ``?redirect=/atlas/...``, internal-user check, bootstrap);
``index`` and ``_login_redirect`` keep Odoo's decision and only map its ``/odoo`` answer.
"""
import json

from odoo import http
from odoo.http import request

from odoo.addons.web.controllers.home import Home
from odoo.addons.web.controllers.webmanifest import WebManifest

from ..app_url import APP_PATH, ODOO_PATH, to_app_url


class NcApplicationPath(Home):

    @http.route(['/web', ODOO_PATH, ODOO_PATH + '/<path:subpath>', '/scoped_app/<path:subpath>',
                 APP_PATH, APP_PATH + '/<path:subpath>'],
                type='http', auth='none', readonly=Home._web_client_readonly)
    def web_client(self, s_action=None, **kw):
        path = request.httprequest.path
        if path == ODOO_PATH or path.startswith(ODOO_PATH + '/'):
            # Every link Odoo generates (e-mails, chatter, its own redirects) keeps working, and never stays in the
            # address bar. A plain 302: the prefix is configuration, a permanent redirect would outlive a change of it.
            return request.redirect(to_app_url(request.httprequest.full_path.rstrip('?')), code=302, local=True)
        return super().web_client(s_action=s_action, **kw)

    @http.route('/', type='http', auth='none')
    def index(self, s_action=None, db=None, **kw):
        response = super().index(s_action=s_action, db=db, **kw)
        location = response.headers.get('Location')
        if location:
            response.headers['Location'] = to_app_url(location)
        return response

    def _login_redirect(self, uid, redirect=None):
        return to_app_url(super()._login_redirect(uid, redirect=redirect))


# The two lines of Odoo's service worker (web/static/src/service_worker.js) that name the application path; pinned by
# ncollection_core/tests/test_app_url.py so an Odoo update that changes them fails instead of silently keeping /odoo.
SW_HOMEPAGE = 'const homepageURL = "/odoo";'
SW_SHARE_TARGET = 'Response.redirect("/odoo?share_target=trigger")'


class NcApplicationPathPwa(WebManifest):
    """The installable app (web manifest + service worker) lives under the application path too: Odoo scopes both to
    /odoo, which would leave every /atlas page outside the app and its offline cache."""

    def _get_webmanifest(self):
        manifest = super()._get_webmanifest()
        manifest['scope'] = manifest['start_url'] = APP_PATH
        for shortcut in manifest.get('shortcuts') or []:
            shortcut['url'] = to_app_url(shortcut.get('url'))
        share = manifest.get('share_target')
        if isinstance(share, dict) and share.get('action'):
            share['action'] = to_app_url(share['action'])
        return manifest

    @http.route()
    def service_worker(self):
        response = super().service_worker()
        response.headers['Service-Worker-Allowed'] = APP_PATH
        return response

    def _get_service_worker_content(self):
        body = super()._get_service_worker_content()
        return (body.replace(SW_HOMEPAGE, 'const homepageURL = %s;' % json.dumps(APP_PATH))
                .replace(SW_SHARE_TARGET, 'Response.redirect(%s)' % json.dumps(APP_PATH + '?share_target=trigger')))
