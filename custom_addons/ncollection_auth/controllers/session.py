# -*- coding: utf-8 -*-
"""Logout logging (P1-T19). Route override calls super() unchanged —
observation only, no session logic is modified."""

from odoo import http
from odoo.http import request

from odoo.addons.web.controllers.session import Session


class SessionAuthLog(Session):

    @http.route()
    def logout(self, redirect='/odoo'):
        # Capture while the session still knows who is logging out.
        if request.session.uid:
            user = request.env['res.users'].sudo().browse(request.session.uid)
            request.env['ncollection.auth.log'].sudo()._capture('logout', user=user)
        return super().logout(redirect=redirect)
