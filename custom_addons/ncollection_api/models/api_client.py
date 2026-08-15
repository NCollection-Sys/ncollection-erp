# -*- coding: utf-8 -*-
"""P8-T01: the OAuth2 client — who may ask for a token, and for what.

CLIENT SECRETS ARE HASHED, using the SAME context core uses for API keys
(`KEY_CRYPT_CONTEXT`, `res.users.apikeys`). Reusing it rather than picking an
algorithm here means the repo has ONE answer to "how do we store a credential",
and it is the one Odoo already reviewed.

The secret is returned exactly once, at creation. There is no path to read it
back — deliberately, and the field carries no getter that could become one.

WHY A SERVICE USER PER CLIENT. Core's key store is keyed on `res.users`, and its
SQL joins `res_users u ON (u.id = user_id) WHERE u.active`. Binding each client
to its own user means:

* deactivating that user kills every token it issued, in SQL, immediately;
* the API acts with that user's ACLs and record rules — so a client cannot read
  anything the corresponding Odoo user could not read. The permission model is
  Odoo's, not a second one invented here, which is what Rule 4 asks for.

RATE LIMIT LIVES HERE, not in a global setting, because "how much traffic is
this integration allowed" is a property of the integration. nginx's `limit_req`
(P1-T03) is the other layer; ARCHITECTURE_SECURITY §6 asks for two independent
ones for auth, and the same reasoning applies to a public API.
"""
import binascii
import logging
import os

from odoo import api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

# Same size core uses for API keys (API_KEY_SIZE = 20 bytes). No reason to
# choose differently, and a reason not to: one number to review.
SECRET_BYTES = 20


class NcollectionApiClient(models.Model):
    _name = 'ncollection.api.client'
    _description = 'NCollection API Client'
    _order = 'name'

    name = fields.Char(required=True)
    client_id = fields.Char(
        required=True, readonly=True, index=True, copy=False,
        default=lambda self: self._nc_new_identifier())
    secret_hash = fields.Char(
        readonly=True, copy=False, groups='base.group_system',
        help="Hashed with the same context core uses for API keys. The "
             "plaintext is shown once at creation and is not recoverable.")
    user_id = fields.Many2one(
        'res.users', string='Acts As', required=True, ondelete='restrict',
        help="The API acts with this user's access rights. A client can never "
             "read what this user could not read — the permission model is "
             "Odoo's, not a second one.")
    scope_ids = fields.Many2many(
        'ncollection.api.scope', string='Allowed Scopes',
        help="The most a token for this client may be granted. A token request "
             "asking for more is refused rather than quietly narrowed.")
    active = fields.Boolean(default=True)
    rate_limit_per_minute = fields.Integer(
        default=60,
        help="Requests per minute for this client. 0 disables the app-layer "
             "limit; nginx's limit_req (P1-T03) still applies.")

    rate_window_start = fields.Datetime(readonly=True, copy=False)
    rate_count = fields.Integer(readonly=True, copy=False)

    def _nc_consume_rate_slot(self):
        """Take one slot. Returns False when the client is over its limit.

        COUNT-AND-RESERVE UNDER A ROW LOCK, not count-then-hope. The first
        version counted rows in the request log and passed if the count was
        under the limit — which cannot work: Odoo runs every cursor at
        REPEATABLE READ (`sql_db.py`), so each concurrent request sees a
        snapshot taken before its siblings' rows existed. N concurrent requests
        therefore all read the same pre-burst count and ALL passed, for any N.
        Not a race that needs luck — a guarantee of snapshot isolation. The
        test could not see it because it called the endpoint twice in sequence.

        `FOR UPDATE` serialises requests for the SAME client; different clients
        never contend. On a serialization failure the answer is "limited",
        because a rate limiter that fails open under load fails exactly when it
        is needed.
        """
        self.ensure_one()
        if self.rate_limit_per_minute <= 0:
            return True
        now = fields.Datetime.now()
        window_floor = fields.Datetime.subtract(now, minutes=1)
        try:
            with self.env.cr.savepoint():
                self.env.cr.execute(
                    "SELECT rate_window_start, rate_count FROM %s "
                    "WHERE id = %%s FOR UPDATE" % self._table, (self.id,))
                row = self.env.cr.fetchone()
                start, count = (row or (None, 0))
                if not start or start < window_floor:
                    start, count = now, 0
                if count >= self.rate_limit_per_minute:
                    return False
                self.env.cr.execute(
                    "UPDATE %s SET rate_window_start = %%s, rate_count = %%s "
                    "WHERE id = %%s" % self._table, (start, count + 1, self.id))
        except Exception:
            # Includes Postgres' serialization failure under REPEATABLE READ.
            # Fail CLOSED.
            _logger.warning("api: rate slot could not be taken for %s; "
                            "refusing rather than failing open", self.client_id)
            return False
        self.invalidate_recordset(['rate_window_start', 'rate_count'])
        return True

    # models.Constraint, NOT the legacy _sql_constraints list: Odoo 19 ignores
    # that list entirely, so constraints declared the old way are silently
    # absent from the database. This repo already records the same finding in
    # ncollection_auth/models/auth_log.py — written the old way here first,
    # and caught only because a test asserted the constraint actually fires.
    _client_id_uniq = models.Constraint(
        'unique(client_id)', "Two clients cannot share a client_id.")
    # Two clients on one service user would share a rate-limit bucket and blur
    # log attribution, and before the token-binding fix would have crossed each
    # other's scopes outright. Flagged in security review.
    _user_uniq = models.Constraint(
        'unique(user_id)', "Each API client needs its own service user.")

    # ---- creation --------------------------------------------------------

    @api.model
    def _nc_new_identifier(self):
        return binascii.hexlify(os.urandom(16)).decode()

    def _nc_rotate_secret(self):
        """Generate a new secret, store only its hash, return the plaintext.

        Returning it is the ONLY time it exists outside the caller. Callers
        must not log it — this method logs the rotation, never the value.
        """
        self.ensure_one()
        crypt = self.env['res.users.apikeys']._nc_crypt_context()
        plaintext = binascii.hexlify(os.urandom(SECRET_BYTES)).decode()
        self.sudo().secret_hash = crypt.hash(plaintext)
        _logger.info("api: client secret rotated for %s (%s)",
                     self.name, self.client_id)
        return plaintext

    # ---- authentication --------------------------------------------------

    @api.model
    def _nc_authenticate(self, client_id, client_secret):
        """The client for these credentials, or an empty recordset.

        Deliberately returns the same empty result for "no such client" and
        "wrong secret". Distinguishing them turns the endpoint into a client_id
        oracle, which is how an attacker enumerates integrations before
        attacking one.
        """
        if not client_id or not client_secret:
            return self.browse()
        client = self.sudo().search(
            [('client_id', '=', client_id), ('active', '=', True)], limit=1)
        if not client or not client.secret_hash:
            # Still spend the time a real verification would, so a missing
            # client is not distinguishable by response time alone.
            self.env['res.users.apikeys']._nc_crypt_context().hash(
                client_secret)
            return self.browse()
        crypt = self.env['res.users.apikeys']._nc_crypt_context()
        if not crypt.verify(client_secret, client.secret_hash):
            return self.browse()
        if not client.user_id.active:
            # The acting user was disabled. Core's SQL would refuse any token
            # issued here anyway; refusing at the door makes the reason visible
            # in the log instead of surfacing as a mysterious 401 later.
            _logger.warning("api: client %s authenticated but its user is "
                            "inactive", client.client_id)
            return self.browse()
        return client

    def _nc_grantable(self, requested_codes):
        """The scopes this client may actually receive.

        A request for a scope the client does not hold is an ERROR, not a
        silent narrowing. Quietly issuing a smaller token than asked for is how
        an integration ends up believing it has permissions it does not, and
        discovering otherwise in production.
        """
        self.ensure_one()
        allowed = set(self.scope_ids.mapped('code'))
        requested = set(requested_codes)
        excess = requested - allowed
        if excess:
            raise ValidationError(self.env._(
                "scope not allowed for this client: %(scopes)s",
                scopes=", ".join(sorted(excess))))
        return self.env['ncollection.api.scope'].search(
            [('code', 'in', sorted(requested))])
