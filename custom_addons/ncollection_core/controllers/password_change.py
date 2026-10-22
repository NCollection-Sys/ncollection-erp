# -*- coding: utf-8 -*-
"""The page a person lands on after an administrator reset their password: choose your own before anything else.

The wall itself is ``ir.http._nc_enforce_password_change`` (every authenticated request); this is the one place it lets
through. The person must give the password they were handed (so a hijacked cookie alone cannot set a new one), and
the new password goes through Odoo's own ``res.users.change_password`` (hashing, password policy). The flag is lifted
only after that succeeded, and the session is ended so the person signs in again with the password they chose.

Nothing typed on this page is logged. Only the fact that the person changed their password is audited.
"""
import logging

from odoo import http
from odoo.exceptions import AccessDenied, UserError
from odoo.http import request

from ..models.ir_http import PASSWORD_CHANGE_PATH

_logger = logging.getLogger(__name__)


class PasswordChange(http.Controller):

    @http.route(PASSWORD_CHANGE_PATH, type='http', auth='user', methods=['GET', 'POST'], readonly=False,
                website=False, sitemap=False)
    def change(self, **post):
        user = request.env['res.users'].sudo().browse(request.session.uid)
        if not user.nc_must_change_password:
            return request.redirect('/odoo')              # nothing pending: this page has no other use
        error = None
        if request.httprequest.method == 'POST':
            error = self._apply(user, post)
            if not error:
                request.session.logout(keep_db=True)
                return request.redirect('/web/login?password_changed=1')
        return request.render('ncollection_core.password_change_page', {'error': error, 'login': user.login})

    @staticmethod
    def _apply(user, post):
        old = post.get('old_password') or ''
        new = post.get('password') or ''
        confirm = post.get('confirm_password') or ''
        env = request.env
        if not old or not new:
            return env._("Fill in all the fields.")
        if new != confirm:
            return env._("The new password and its confirmation are not the same.")
        if new == old:
            return env._("Choose a password different from the one you were given.")
        try:
            env['res.users'].change_password(old, new)
        except AccessDenied:
            return env._("The password you were given is not correct.")
        except UserError as exc:
            return str(exc)
        user._nc_clear_must_change()
        log = env.get('ncollection.auth.log')
        if log is not None:
            log._capture('password_changed', user=user)
        return None
