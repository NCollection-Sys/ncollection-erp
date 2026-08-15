# -*- coding: utf-8 -*-
"""P8-T01: access tokens, built ON TOP of Odoo core's key store.

WHY NOT A NEW CREDENTIAL STORE. Odoo 19 core's `res.users.apikeys`
(`base/models/res_users.py:1520`) already does the hard, easy-to-get-wrong part:

* the secret is stored **hashed** (`KEY_CRYPT_CONTEXT.hash`) and is **not an ORM
  field** — the model is `_auto = False` with a hand-written table, so the key
  column cannot be read back through the ORM at all;
* an 8-hex-digit plaintext `index` makes lookup cheap without storing the secret;
* expiry is enforced **in SQL**, alongside `u.active`, so a disabled user's token
  dies with them;
* removal is identity-checked and logs the client IP.

Writing a second credential store next to that would be reinventing a reviewed
one, which Standing Rule 2 exists to prevent.

THE ONE THING CORE CANNOT DO, and why this model exists. Core's check is:

    AND (scope IS NULL OR scope = %(scope)s)

One scope, matched exactly — and `NULL` means *everything*. OAuth2 tokens carry
a SET of scopes, which that column cannot express. So the split is:

    core   proves the token is real, unexpired, and belongs to a live user
    here   decides what that token is allowed to do

A SECURITY PROPERTY THAT FALLS OUT OF THIS, and is asserted by a test rather
than assumed: every token this module issues is written with
`scope = 'ncollection_api'`. Odoo's own RPC authentication checks
`scope='rpc'`, and core's SQL requires `scope IS NULL OR scope = 'rpc'`. Ours is
neither — so **an API token cannot be replayed as a general RPC credential.**
It reaches this module's routes and nothing else. A token with a NULL scope
would have been a master key to the whole ORM; that is why the scope is never
left unset.
"""
import logging

from odoo import api, fields, models
from odoo.addons.base.models.res_users import INDEX_SIZE

_logger = logging.getLogger(__name__)

# The namespace this module writes into core's single-scope column. NEVER None:
# a NULL scope authenticates against every scope check in Odoo, including RPC.
NC_API_SCOPE = 'ncollection_api'


class NcollectionApiToken(models.Model):
    _name = 'ncollection.api.token'
    _description = 'NCollection API Access Token'
    _order = 'id desc'
    _rec_name = 'display_name'

    client_id = fields.Many2one(
        'ncollection.api.client', required=True, ondelete='cascade',
        index=True, readonly=True)
    # The core row that holds the actual secret. Deleting the token record must
    # delete the credential, not orphan it — see unlink().
    apikey_id = fields.Integer(
        string='Core API key id', required=True, readonly=True, index=True,
        help="Row id in res_users_apikeys. Not a Many2one: that model is "
             "_auto=False with a hand-written table, so it carries no "
             "ir_model_data and cannot be a relational target.")
    scope_ids = fields.Many2many(
        'ncollection.api.scope', string='Granted Scopes', readonly=True)
    expires_at = fields.Datetime(readonly=True, index=True)
    revoked_at = fields.Datetime(readonly=True)

    def _compute_display_name(self):
        for token in self:
            token.display_name = "%s [%s]" % (
                token.client_id.name or '',
                ", ".join(sorted(token.scope_ids.mapped('code'))) or 'no scope')

    # ---- issuing ---------------------------------------------------------

    @api.model
    def _nc_issue(self, client, scopes, ttl_seconds):
        """Mint a token for ``client``. Returns ``(record, plaintext)``.

        The plaintext is returned ONCE and never stored — core hashes it on
        insert and there is no way to read it back, which is the property that
        makes a leaked database less than a leaked credential.
        """
        expires_at = fields.Datetime.add(
            fields.Datetime.now(), seconds=ttl_seconds)
        # sudo: `_generate` inserts for `self.env.user`, and the caller here is
        # a public, unauthenticated controller running as the public user. The
        # token must belong to the CLIENT's service user, not to whoever
        # happened to hit the endpoint.
        keys = self.env['res.users.apikeys'].with_user(
            client.sudo().user_id).sudo()
        plaintext = keys._generate(NC_API_SCOPE, "API token: %s" % client.name,
                                   expires_at)
        record = self.sudo().create({
            'client_id': client.id,
            'apikey_id': self._nc_apikey_id_for(
                client.sudo().user_id, plaintext),
            'scope_ids': [(6, 0, scopes.ids)],
            'expires_at': expires_at,
        })
        _logger.info("api: token issued for client %s scopes=%s ttl=%ss",
                     client.name, sorted(scopes.mapped('code')), ttl_seconds)
        return record, plaintext

    @api.model
    def _nc_apikey_id_for(self, user, plaintext):
        """The core row that holds THIS plaintext, found by its own index.

        Core stores the first `INDEX_SIZE` hex characters of the key in a
        plaintext `index` column precisely so a row can be located without the
        secret. Using it means the lookup names the exact credential.

        The first version instead took the newest row for the user
        (`ORDER BY id DESC LIMIT 1`) and justified it with a transaction lock
        that does not exist. It happened to be correct — Odoo runs cursors at
        REPEATABLE READ, so a sibling's row is either invisible or older — but
        that is an emergent property of a global default, not something this
        method enforced. Security review called the reasoning wrong while the
        code was right, which is the kind of comment that survives until the
        default changes.
        """
        self.env.cr.execute(
            "SELECT id FROM res_users_apikeys WHERE user_id = %s AND index = %s",
            (user.id, plaintext[:INDEX_SIZE]))
        row = self.env.cr.fetchone()
        return row[0] if row else 0

    # ---- verifying -------------------------------------------------------

    @api.model
    def _nc_authenticate(self, plaintext):
        """``(token, uid)`` for a bearer string, or ``(empty, None)``.

        Core does the cryptography — hash comparison, index lookup, expiry and
        `u.active` — in one SQL statement. This adds nothing to that check and
        deliberately does not try to.
        """
        if not plaintext:
            return self.browse(), None
        uid = self.env['res.users.apikeys']._check_credentials(
            scope=NC_API_SCOPE, key=plaintext)
        if not uid:
            return self.browse(), None
        # Core validated the SECRET and returns only a uid — NOT which row
        # matched. Resolving our record by "newest live token for that user"
        # was a privilege-escalation waiting for a second scope to exist:
        # a client holding token A (scope S1) and token B (scope S2) could
        # present A and be handed B's grants, because A and B share a user.
        # Impact was nil while one scope shipped, and would have arrived with
        # P8-T02. Found in security review.
        #
        # So bind to the credential actually presented, by its own index.
        apikey_id = self._nc_apikey_id_for(
            self.env['res.users'].sudo().browse(uid), plaintext)
        if not apikey_id:
            return self.browse(), None
        token = self.sudo().search([
            ('apikey_id', '=', apikey_id),
            ('revoked_at', '=', False),
            ('expires_at', '>', fields.Datetime.now()),
        ], limit=1)
        if not token:
            return self.browse(), None
        return token, uid

    def _nc_has_scope(self, code):
        self.ensure_one()
        return code in self.scope_ids.mapped('code')

    # ---- revocation ------------------------------------------------------

    def _nc_revoke(self):
        """Revoke here AND delete the core key, so a revoked token is dead at
        the cryptographic layer too, not merely flagged at ours."""
        for token in self:
            if token.apikey_id:
                self.env.cr.execute(
                    "DELETE FROM res_users_apikeys WHERE id = %s",
                    (token.apikey_id,))
            token.sudo().revoked_at = fields.Datetime.now()
        # Core caches credential lookups; a revoked key must stop working now,
        # not at the next registry reload.
        self.env.registry.clear_cache()
        return True

    def unlink(self):
        """Deleting the record must not orphan a live credential in core's
        table — that would leave a working token nobody can see."""
        self._nc_revoke()
        return super().unlink()
