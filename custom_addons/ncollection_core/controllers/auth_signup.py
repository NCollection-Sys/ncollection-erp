# -*- coding: utf-8 -*-
"""Public signup endpoint for the standalone demo UI.

DEMO SCOPE ONLY (Refs GitHub issue #103): this endpoint lets the React demo
create a workspace user with no gatekeeping. The production signup flow is
owned by P2-T16 (public checkout: rate limiting, email verification, trial
quotas, CAPTCHA) and P1-T19 (auth hardening).

SECURITY (P3-T12 / finding C-1): ncollection_core ships in EVERY tenant
(CORE_TENANT_MODULES), so this route is reachable on every tenant subdomain.
Left open it lets an unauthenticated caller create an Internal User in a live
tenant's ERP (bypassing the Owner-only invite wizard, and the seat limit via
sudo). It is therefore **DISABLED by default** (secure by absence) and only runs where
`ncollection_core.public_signup_enabled` is explicitly enabled — which no
provisioned tenant does. The demo/dev `ncollection` DB must enable it manually;
that bootstrap wiring + the demo's `signup_disabled` handling are tracked in #226.
"""

import logging
import re

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LENGTH = 8
# Off unless explicitly enabled (secure by absence). Only the demo/dev DB sets it.
SIGNUP_FLAG = 'ncollection_core.public_signup_enabled'
_TRUTHY = ('true', '1', 'yes', 'on')


class NCollectionAuthSignup(http.Controller):

    @http.route('/ncollection/api/signup', type='jsonrpc', auth='public',
                methods=['POST'], readonly=False)
    def signup(self, name=None, email=None, password=None):
        """Create a workspace user and return a JSON result.

        Returns ``{'success': True}`` or ``{'success': False, 'error': <code>}``
        where ``<code>`` is a stable key the frontend maps to a translated
        message: missing_fields | invalid_email | weak_password | email_exists.
        Returns ``signup_disabled`` where the public-signup flag is off (default).
        """
        enabled = (request.env['ir.config_parameter'].sudo()
                   .get_param(SIGNUP_FLAG, 'False') or '').strip().lower()
        if enabled not in _TRUTHY:
            _logger.warning(
                "Blocked public signup attempt (flag off) from %s",
                request.httprequest.remote_addr)
            return {'success': False, 'error': 'signup_disabled'}

        name = (name or '').strip()
        email = (email or '').strip().lower()

        if not name or not email or not password:
            return {'success': False, 'error': 'missing_fields'}
        if not EMAIL_RE.match(email):
            return {'success': False, 'error': 'invalid_email'}
        if len(password) < MIN_PASSWORD_LENGTH:
            return {'success': False, 'error': 'weak_password'}

        Users = request.env['res.users'].sudo().with_context(active_test=False)
        if Users.search_count([('login', '=', email)]):
            return {'success': False, 'error': 'email_exists'}

        # Odoo 19 note: the old `base.default_user` template was removed and
        # the groups m2m was renamed `groups_id` -> `group_ids`. Mirroring
        # v19's own auth_signup fallback: plain create() with an explicit
        # Internal User group. The `password` value is hashed by the field's
        # inverse; plaintext is never stored.
        group_user = request.env.ref('base.group_user')
        user = request.env['res.users'].sudo().create({
            'name': name,
            'login': email,
            'email': email,
            'password': password,
            'group_ids': [(6, 0, [group_user.id])],
        })

        _logger.info("Demo signup: created user %s (id %s)", email, user.id)
        return {'success': True}
