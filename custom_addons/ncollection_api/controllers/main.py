# -*- coding: utf-8 -*-
"""P8-T01 / P8-T02: the public surface — /api/v1, OAuth2 client-credentials, scopes.

ROUTE TYPE. These are `type='http'`, not `type='jsonrpc'`. Odoo's jsonrpc
transport wraps everything in its own envelope and answers errors with HTTP
200, which is fine for the web client and wrong for a public REST API where the
status code IS the contract. CLAUDE.md's note that "HTTP JSON routes are
`type='jsonrpc'`" is about Odoo's own RPC surface; this is deliberately not
that.

AUTH. `auth='none'` on every route, and that is not a gap — it is the point.
`auth='user'` would authenticate against Odoo's SESSION, which is a cookie, and
a public API must not accept one: a browser holding a logged-in session would
otherwise be able to drive the API cross-site. Every route here authenticates
the BEARER TOKEN itself and then acts as the client's user via `with_user`.

ERROR ENVELOPE. One shape, everywhere:

    {"error": {"code": "invalid_scope", "message": "..."}}

`code` is stable and machine-readable; `message` is for humans and may change.
Success bodies carry the payload directly, with no wrapper — a wrapper on
success and an envelope on failure is the shape most clients expect, and it
keeps `data.data` out of the world.

READONLY=FALSE, AND WHY IT IS NOT OPTIONAL. Odoo 19 decides a route's
transaction mode like this (`odoo/http.py:974`):

    default_mode = submethod.original_routing.get('readonly', default_auth == 'none')

So `auth='none'` — every route here — defaults to a READ-ONLY transaction.
Every request-log INSERT then failed with
`psycopg2.errors.ReadOnlySqlTransaction`, caught by `_log`'s own guard and
logged rather than raised. The API kept answering correctly, so nothing looked
wrong; but the log was empty, and the rate limiter COUNTS THAT LOG, so it was
counting zero and would never have limited anything. Found because `make test`
greps the log for tracebacks — the response bodies were all correct.

WHAT IS DELIBERATELY NOT HERE. The authorization-code flow. It needs a consent
screen, redirect-URI validation and PKCE, and it exists to let a THIRD-PARTY
app act for a user — which is marketplace territory (Phase 9), not the
machine-to-machine integration this ticket's acceptance criterion describes.
Half-building it would ship a login-shaped surface with none of the protections
that make one safe. Filed separately.
"""
import time

from odoo import http
from odoo.exceptions import ValidationError
from odoo.http import request

from .common import (
    API_ROOT,
    TOKEN_TTL_SECONDS,
    ApiControllerBase,
    _error,
    _json,
)


class NcollectionApiV1(ApiControllerBase):

    # ---- helpers ---------------------------------------------------------

    def _throttle_response(self, route, started, source):
        """``None`` when ``source`` may proceed, else a ready 429 ``Response``."""
        throttle = request.env['ncollection.api.throttle'].sudo()
        if not throttle._nc_is_throttled(source):
            return None
        self._log(route, 429, started)
        return _error('too_many_failed_attempts',
                      "too many failed authentication attempts; try again "
                      "later", 429)

    def _resolve_scopes(self, client, post, route, started):
        """``(scopes, None)`` when grantable, else ``(None, Response)``."""
        requested = (post.get('scope') or '').split()
        try:
            scopes = client._nc_grantable(requested)
        except ValidationError as exc:
            self._log(route, 400, started, client=client)
            return None, _error('invalid_scope', str(exc), 400)
        if not scopes:
            self._log(route, 400, started, client=client)
            return None, _error('invalid_scope',
                                "at least one scope is required", 400)
        return scopes, None

    # ---- OAuth2: client credentials --------------------------------------

    @http.route('%s/oauth/token' % API_ROOT, type='http', auth='none',
                methods=['POST'], csrf=False, save_session=False,
                readonly=False)
    def oauth_token(self, **post):
        """RFC 6749 §4.4 — client credentials."""
        started = time.monotonic()
        route = '%s/oauth/token' % API_ROOT
        grant = post.get('grant_type')
        if grant != 'client_credentials':
            self._log(route, 400, started)
            return _error('unsupported_grant_type',
                          "only client_credentials is supported; the "
                          "authorization_code flow is not implemented", 400)

        source = self._remote_addr()
        throttled = self._throttle_response(route, started, source)
        if throttled is not None:
            return throttled

        client = request.env['ncollection.api.client'].sudo()._nc_authenticate(
            post.get('client_id'), post.get('client_secret'))
        if not client:
            request.env['ncollection.api.throttle'].sudo()._nc_record_failure(
                source)
            self._log(route, 401, started)
            return _error('invalid_client', "client authentication failed", 401)

        if self._rate_limited(client):
            self._log(route, 429, started, client=client)
            return _error('rate_limited', "too many requests", 429)

        scopes, refusal = self._resolve_scopes(client, post, route, started)
        if refusal is not None:
            return refusal

        _token, plaintext = request.env['ncollection.api.token'].sudo()._nc_issue(
            client, scopes, TOKEN_TTL_SECONDS)
        self._log(route, 200, started, client=client)
        return _json({
            'access_token': plaintext,
            'token_type': 'Bearer',
            'expires_in': TOKEN_TTL_SECONDS,
            'scope': " ".join(sorted(scopes.mapped('code'))),
        })
