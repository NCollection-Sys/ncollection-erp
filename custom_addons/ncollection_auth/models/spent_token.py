# -*- coding: utf-8 -*-
"""Single-use emailed reset/signup links.

Odoo 19's emailed token is a stateless signed payload (partner, users, last login, type) with an expiry. It stops
working when the user LOGS IN, but not when the password is merely reset by it, so a link stays good for its whole
validity window (4 h for a reset). This keeps the SHA-256 of each token that has been used; nothing is stored in the
clear and the token itself is never logged.
"""

import hashlib
import logging
from datetime import timedelta

from odoo import api, fields, models
from odoo.tools.sql import table_exists

_logger = logging.getLogger(__name__)

_KEEP_DAYS = 7    # far beyond any token's validity (reset: 4 h, signup: 6 days by default)


class NCollectionSpentToken(models.Model):
    _name = 'ncollection.auth.spent_token'
    _description = 'Used password-reset / signup link'
    _log_access = False

    digest = fields.Char(required=True, index=True, readonly=True)
    used_at = fields.Datetime(required=True, default=fields.Datetime.now, readonly=True)

    _digest_unique = models.Constraint('unique(digest)', 'A token is recorded once.')

    @staticmethod
    def _digest_of(token):
        return hashlib.sha256((token or '').encode()).hexdigest()

    @api.model
    def _is_spent(self, token):
        if not table_exists(self.env.cr, self._table):
            # code ahead of schema (a tenant not yet upgraded): fail CLOSED, never accept a link that cannot be
            # proven unused
            _logger.error("%s is missing: upgrade ncollection_auth on this database; reset links are refused.",
                          self._table)
            return True
        return bool(self.sudo().search_count([("digest", "=", self._digest_of(token))]))

    @api.model
    def _spend(self, token):
        if not table_exists(self.env.cr, self._table):
            return
        self = self.sudo()
        self.search([("used_at", "<", fields.Datetime.now() - timedelta(days=_KEEP_DAYS))]).unlink()
        if not self._is_spent(token):
            self.create({"digest": self._digest_of(token)})
