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

from odoo import models
from odoo.http import request

_logger = logging.getLogger(__name__)

_OWNER_GROUP = 'ncollection_core.group_role_owner'


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
            user = request.env.user
            if request.env.su or user._is_superuser():
                return
            if user.has_group(_OWNER_GROUP):
                return
        except Exception:  # pragma: no cover - never break dispatch
            # Uncertain -> fall through and fail closed (strip debug).
            _logger.debug("Owner debug-gate check failed; stripping debug", exc_info=True)
        request.session.debug = ''
