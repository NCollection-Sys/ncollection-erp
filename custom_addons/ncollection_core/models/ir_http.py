# -*- coding: utf-8 -*-
"""Debug-mode lockdown for non-Owner tenant users (P1-T11).

Odoo activates developer mode from the ``?debug=`` query string in
``ir.http._handle_debug`` (verified against Odoo 19 web/models/ir_http.py).
Developer mode exposes technical menus, external IDs, and the module
manager surface — a tenant user must not be able to switch it on. We wrap
``_handle_debug`` and clear any requested debug flag unless the current
user is the tenant Owner (or the true superuser).

FAIL-CLOSED for debug specifically: on any uncertainty we leave debug OFF
(the safe state), then delegate to super for the normal path. This never
blocks dispatch — it only refuses to *enable* debug.
"""

import logging

import werkzeug.exceptions

from odoo import models
from odoo.exceptions import AccessError
from odoo.http import request
from odoo.tools import consteq

from ..app_url import APP_PATH

_logger = logging.getLogger(__name__)

_OWNER_GROUP = 'ncollection_core.group_role_owner'

# While a password reset by an administrator is pending, the signed-in person may reach only what is needed to choose a
# new password (or leave): the change page, the sign-out, and the static files that page is made of.
PASSWORD_CHANGE_PATH = '/ncollection/password/change'
_PASSWORD_CHANGE_ALLOWED = (PASSWORD_CHANGE_PATH, '/web/session/logout', '/web/session/destroy')
_PASSWORD_CHANGE_ALLOWED_PREFIXES = ('/web/assets/', '/web/static/', '/web/image/', '/web/binary/company_logo')


class IrHttp(models.AbstractModel):
    _inherit = 'ir.http'

    @classmethod
    def _handle_debug(cls):
        super()._handle_debug()
        if request is None:
            return
        # No debug requested / already off -> nothing to strip.
        if not request.session.debug:
            return
        try:
            user = cls._nc_debug_user()                             # None: no valid session -> fail closed below
            if user is not None and (request.env.su or user._is_superuser()):
                return
            if user is not None and user.has_group(_OWNER_GROUP):
                return
            # The platform administrator runs the platform database and never holds the tenant Owner ROLE, so
            # Settings > Activate the developer mode was silently undone for exactly the operator it exists for. The
            # check is the one module management uses, and it is always False inside a tenant database.
            if user is not None and user._nc_actor_is_platform_admin():
                return
        except Exception:  # pragma: no cover - never break dispatch
            # Uncertain -> fall through and fail closed (strip debug).
            _logger.debug("Owner debug-gate check failed; stripping debug", exc_info=True)
        request.session.debug = ''

    @classmethod
    def _nc_debug_user(cls):
        """The signed-in user the debug flag is decided for, or None.

        The web client (``/odoo``, ``/atlas``, ``/web``) is an ``auth='none'`` route, as in Odoo, so during dispatch
        ``request.env.user`` is NOT the signed-in user: the gate judged "nobody" and stripped ``?debug=1`` for every
        user, the Owner included, which is why Settings > Activate the developer mode never stuck. On such a route the
        session's user is taken, but only after the same session-token check Odoo's own web client makes
        (``security.check_session``, without its side effects): a stale or forged session fails closed."""
        if request.env.uid:
            return request.env.user
        uid = request.session.uid
        if not uid:
            return None
        user = request.env['res.users'].sudo().browse(uid).exists()
        if not user or not user.active:
            return None
        expected = user._compute_session_token(request.session.sid)
        if not expected or not consteq(expected, request.session.session_token or ''):
            return None
        return user.with_user(user).sudo(False)

    @classmethod
    def _authenticate(cls, endpoint):
        result = super()._authenticate(endpoint)
        cls._nc_enforce_password_change()
        return result

    @classmethod
    def _nc_enforce_password_change(cls):
        """A session whose password an administrator reset can do nothing but set its own password first.

        Enforced on EVERY authenticated request here, not by the sign-in page: a person who already holds a session
        cookie, or who calls the JSON routes directly, meets the same wall. Web pages are sent to the change page; the
        RPC routes are refused. The flag is read as the superuser: the person may not be allowed to read their own row.
        """
        if request is None or not request.session.uid:
            return
        path = request.httprequest.path
        if path in _PASSWORD_CHANGE_ALLOWED or path.startswith(_PASSWORD_CHANGE_ALLOWED_PREFIXES):
            return
        if not request.env['res.users'].sudo().browse(request.session.uid).nc_must_change_password:
            return
        if request.dispatcher.routing_type == 'jsonrpc':
            raise AccessError(request.env._("You must choose a new password before you continue."))
        raise werkzeug.exceptions.HTTPException(response=request.redirect(PASSWORD_CHANGE_PATH, 303))

    def session_info(self):
        """Tell the web client when this session is a Platform Administrator's, so it can say so on every screen."""
        info = super().session_info()
        info['nc_app_path'] = APP_PATH            # the public application path (app_url.py), for the router
        user = self.env.user
        if request and request.session.uid and user.nc_platform_proxy:
            info['nc_platform_mode'] = {
                'company': self.env.company.name,
                'admin': self.env['ncollection.platform.entry']._session_admin_name(user),
                'exit_url': '/ncollection/platform/exit',
            }
        return info
