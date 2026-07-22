# -*- coding: utf-8 -*-
"""Suspended/expired workspace interstitial (P2-T03).

When the platform suspends or expires a tenant's subscription, the config-sync
channel writes that status into ncollection.workspace.config.subscription_status
(the tenant-local projection — no platform round-trip at request time). This
gate then serves a branded "Subscription Expired" page in place of the backend
web client, so a lapsed tenant cannot keep working while renewal happens on the
platform side.

FAIL-OPEN (house style, matching ir_http.py / license_enforcement.py): on any
uncertainty the request proceeds normally. Only the web-client ENTRY routes
(/odoo, /web) are gated — login, logout, password reset, assets, RPC and the
websocket are never touched, so the owner can always log in/out and the page's
own assets load.
"""

import logging

import werkzeug.exceptions

from odoo import SUPERUSER_ID, models
from odoo.http import request

_logger = logging.getLogger(__name__)

# Statuses that block the workspace (projected from tenant.status).
_BLOCKED_STATUSES = frozenset({'suspended', 'expired'})


class IrHttp(models.AbstractModel):
    _inherit = 'ir.http'

    @classmethod
    def _is_web_client_entry(cls):
        """True only for the backend web-client HTML entry points — NOT its
        assets (/web/assets/...), the login page (/web/login) or any RPC."""
        path = request.httprequest.path
        return path == '/odoo' or path.startswith('/odoo/') or path == '/web'

    @classmethod
    def _pre_dispatch(cls, rule, args):
        super()._pre_dispatch(rule, args)
        try:
            if request is None:
                return
            # The web-client route is auth="none", so the logged-in user is on
            # the SESSION, not request.env. Anonymous (login page) and the
            # technical superuser (maintenance) are never gated.
            uid = request.session.uid
            if not uid or uid == SUPERUSER_ID:
                return
            if not cls._is_web_client_entry():
                return
            config = request.env['ncollection.workspace.config'].sudo().get_config()
            status = config.subscription_status if config else False
            if status in _BLOCKED_STATUSES:
                page = request.render('ncollection_core.subscription_blocked', {
                    'status': status,
                    'company': request.env.company,
                })
                # request.render returns a LAZY response, normally flattened at
                # _dispatch; we abort before that, so render the body now.
                page.flatten()
                werkzeug.exceptions.abort(page)
        except werkzeug.exceptions.HTTPException:
            raise  # our own abort(page) — let it through
        except Exception:  # noqa: BLE001 - never break dispatch on the gate
            _logger.debug("Subscription gate check failed; allowing request", exc_info=True)
