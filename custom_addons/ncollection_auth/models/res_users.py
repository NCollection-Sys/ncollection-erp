# -*- coding: utf-8 -*-
"""Logging-only hooks on res.users (P1-T19).

These wrappers NEVER alter authentication behavior — they observe the
outcome and record it in ncollection.auth.log. Enforcement stays with:
- Odoo core's login cooldown (base.login_cooldown_after/_duration), and
- the Nginx edge rate limit (P1-T03).
"""

from odoo import api, models
from odoo.exceptions import AccessDenied


class ResUsers(models.Model):
    _inherit = 'res.users'

    def _check_credentials(self, credential, env):
        """Log login success/failure around core credential checking.

        Failures are written via an isolated cursor because the AccessDenied
        below rolls back the request transaction. Note: core calls this on
        interactive logins and on auth-cache misses, not on every request.
        """
        try:
            result = super()._check_credentials(credential, env)
        except AccessDenied:
            self.env['ncollection.auth.log']._capture_isolated(
                'login_failed', login=self.login,
            )
            raise
        self.env['ncollection.auth.log']._capture('login_success', user=self)
        return result

    def _action_reset_password(self, signup_type="reset"):
        """Log reset requests (covers the UI button and the /web/reset_password
        route). signup_type='signup' is a new-user invite, not a reset."""
        result = super()._action_reset_password(signup_type=signup_type)
        if signup_type == "reset":
            for user in self:
                self.env['ncollection.auth.log']._capture('reset_request', user=user)
        return result

    @api.model
    def signup(self, values, token=None):
        """Log completed password resets (token consumption sets the new
        password). Token-less calls are plain signups — not logged here."""
        result = super().signup(values, token=token)
        if token:
            login = result[1] if isinstance(result, (list, tuple)) and len(result) > 1 \
                else values.get('login')
            self.env['ncollection.auth.log']._capture('reset_complete', login=login)
        return result
