# -*- coding: utf-8 -*-
"""Forgot-password request step: the same answer for every login, and an honest one when mail cannot be sent.

Core's `/web/reset_password` answers "No account found for this login" for an unknown login and a success message for a
known one, which lets anyone list a tenant's logins. The TOKEN step (the emailed link) is left to core untouched: its
token is per database, expires (`auth_signup.reset_password.validity`), is cleared on use, and is written only to the
partner row, so a token from another tenant, an expired one and a reused one are all refused there.

Nothing here reads or writes a password, and nothing logs a token.
"""

import logging

from odoo import http
from odoo.http import request

from odoo.addons.auth_signup.controllers.main import AuthSignupHome
from odoo.addons.web.controllers.utils import ensure_db

_logger = logging.getLogger(__name__)


class NCollectionResetPassword(AuthSignupHome):

    @http.route()
    def web_auth_reset_password(self, *args, **kw):
        if not request.session.uid:
            lang = self._nc_page_lang()
            if lang:
                request.update_context(lang=lang)
        if request.httprequest.method != 'POST' or request.params.get('token'):
            return super().web_auth_reset_password(*args, **kw)
        qcontext = self.get_auth_signup_qcontext()
        if not qcontext.get('reset_password_enabled'):
            return super().web_auth_reset_password(*args, **kw)
        login = (qcontext.get('login') or '').strip()
        if not login:
            return super().web_auth_reset_password(*args, **kw)
        Users = request.env['res.users'].sudo()
        if not Users._nc_reset_mail_available():
            # A property of the workspace, identical for every login, so it reveals nothing about any account.
            qcontext['error'] = request.env._(
                "Password reset by email is not set up for this workspace. Please ask your administrator to reset it.")
            return request.render('auth_signup.reset_password', qcontext)
        try:
            Users.reset_password(login)
        except Exception:    # unknown login, no email on file, ambiguous login, delivery failure: all look the same
            _logger.info("Password reset for an unknown login or an account without a usable email (not disclosed)")
        qcontext['message'] = request.env._(
            "If an account matches, password reset instructions have been sent to its email address.")
        response = request.render('auth_signup.reset_password', qcontext)
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        return response

    # ----------------------------------------------------------- the sign-in page follows the visitor's language
    @staticmethod
    def _nc_page_lang():
        """The language this public page should be shown in: ``?lang=`` (the switch on the page), else the browser's.

        Only an INSTALLED language is ever used, so nothing here can name a language the database does not have."""
        installed = {code for code, _name, *_rest in request.env['res.lang'].sudo().get_installed()}
        for candidate in (request.params.get('lang'), request.best_lang):
            if not candidate:
                continue
            if candidate in installed:
                return candidate
            # the browser says ar_AE, the database has ar_001: same language, so use the installed one
            same_language = sorted(code for code in installed if code.split("_")[0] == candidate.split("_")[0])
            if same_language:
                return same_language[0]
        return None

    @http.route()
    def web_login(self, *args, **kw):
        ensure_db()
        if not request.session.uid:
            lang = self._nc_page_lang()
            if lang:
                request.update_context(lang=lang)
        response = super().web_login(*args, **kw)
        qcontext = getattr(response, 'qcontext', None)
        if qcontext is not None:
            # a plain link to the other language (named in its own language, so it is findable from either page)
            installed = {code for code, _name, *_rest in request.env['res.lang'].sudo().get_installed()}
            current = request.env.lang
            target = 'en_US' if current.startswith('ar') else 'ar_001'
            if target in installed:
                qcontext['nc_lang_toggle'] = {'code': target, 'name': 'English' if target == 'en_US' else 'العربية'}
        return response
