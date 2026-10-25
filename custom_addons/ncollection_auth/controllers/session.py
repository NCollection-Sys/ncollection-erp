# -*- coding: utf-8 -*-
"""Logout logging (P1-T19). Route override calls super() unchanged —
observation only, no session logic is modified."""

from odoo import http
from odoo.http import request

from odoo.addons.web.controllers.session import Session


class SessionAuthLog(Session):

    # readonly=False: Odoo declares this route read-only, and the audit row below is a write. On the read-only
    # cursor it failed, Odoo retried after the session was already gone, and the user got HTTP 403 instead of the
    # login page.
    @http.route(readonly=False)
    def logout(self, redirect=None):
        # Capture while the session still knows who is logging out.
        if request.session.uid:
            user = request.env['res.users'].sudo().browse(request.session.uid)
            request.env['ncollection.auth.log'].sudo()._capture('logout', user=user)
        # the application's public path (ncollection_core/app_url.py: server-wide, so always importable)
        from odoo.addons.ncollection_core.app_url import app_url, to_app_url  # noqa: PLC0415
        return super().logout(redirect=to_app_url(redirect) if redirect else app_url())
