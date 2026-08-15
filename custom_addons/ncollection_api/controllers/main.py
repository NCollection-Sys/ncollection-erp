# -*- coding: utf-8 -*-
"""P8-T01: the public surface — /api/v1, OAuth2 client-credentials, scopes.

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
import json
import logging
import time

from odoo import http
from odoo.exceptions import AccessError, ValidationError
from odoo.http import Response, request

_logger = logging.getLogger(__name__)

API_ROOT = '/api/v1'
TOKEN_TTL_SECONDS = 3600


def _json(payload, status=200):
    return Response(json.dumps(payload), status=status,
                    content_type='application/json; charset=utf-8')


def _error(code, message, status):
    """The ONE error shape. Every failure path goes through here."""
    return _json({'error': {'code': code, 'message': message}}, status=status)


class NcollectionApiV1(http.Controller):

    # ---- helpers ---------------------------------------------------------

    def _remote_addr(self):
        try:
            return request.httprequest.remote_addr or ''
        except (AttributeError, RuntimeError):
            return ''

    def _log(self, route, status, started, client=None, scope=None):
        """Metadata only. Never the body, never the Authorization header."""
        try:
            request.env['ncollection.api.request.log'].sudo().create({
                'client_id': client.id if client else False,
                'route': route,
                'method': request.httprequest.method,
                'status': status,
                'remote_addr': self._remote_addr(),
                'duration_ms': int((time.monotonic() - started) * 1000),
                'scope_checked': scope or '',
            })
        except Exception:
            # A logging failure must never turn a successful API call into a
            # 500. Same reasoning as the audit-trail digest in #81: the
            # response is the point, the log line is a detail.
            _logger.exception("api: could not write request log for %s", route)

    def _rate_limited(self, client):
        """True when this client is over its own per-minute allowance.

        Delegates to a COUNT-AND-RESERVE under a row lock — see
        `ncollection.api.client._nc_consume_rate_slot`. Counting the request
        log instead was bypassable by any concurrent burst, because Odoo's
        cursors run at REPEATABLE READ and every sibling read the same
        pre-burst snapshot.

        `0` disables this layer only; nginx's limit_req (P1-T03) is the other,
        independent one — the two-layer shape ARCHITECTURE_SECURITY §6 asks for.
        """
        if not client:
            return False
        return not client.sudo()._nc_consume_rate_slot()

    def _authenticate(self):
        """``(token, uid)`` from the Authorization header, or ``(None, None)``."""
        header = request.httprequest.headers.get('Authorization') or ''
        if not header.startswith('Bearer '):
            return None, None
        token, uid = request.env['ncollection.api.token'].sudo()._nc_authenticate(
            header[len('Bearer '):].strip())
        return (token or None), uid

    # ---- OAuth2: client credentials --------------------------------------

    @http.route('%s/oauth/token' % API_ROOT, type='http', auth='none',
                methods=['POST'], csrf=False, save_session=False,
                readonly=False)
    def oauth_token(self, **post):
        """RFC 6749 §4.4 — client credentials.

        `csrf=False` is correct and not a shortcut: CSRF protection defends a
        SESSION-authenticated form post. This endpoint has no session (see
        `save_session=False`) and authenticates the client secret in the body,
        so there is no ambient authority for a forged cross-site request to
        borrow.
        """
        started = time.monotonic()
        route = '%s/oauth/token' % API_ROOT
        grant = post.get('grant_type')
        if grant != 'client_credentials':
            self._log(route, 400, started)
            return _error('unsupported_grant_type',
                          "only client_credentials is supported; the "
                          "authorization_code flow is not implemented", 400)

        # #436: throttle BEFORE any credential work. Every failed attempt below
        # costs a PBKDF2 — including the deliberate dummy hash on the
        # unknown-client path — and until now nothing bounded how often that
        # could be provoked. The check is a dict lookup, so the successful path
        # pays essentially nothing for it.
        throttle = request.env['ncollection.api.throttle'].sudo()
        source = self._remote_addr()
        if throttle._nc_is_throttled(source):
            self._log(route, 429, started)
            # A DISTINCT code from the per-client `rate_limited`, because the
            # correct client behaviour differs: `rate_limited` means back off
            # and retry, this means the credentials are wrong and retrying will
            # not help. Same status, different remedy, so a machine-readable
            # code that conflated them would be useless to an integrator.
            # It leaks nothing: the per-client limiter is only reachable AFTER
            # authenticating, so an attacker who never authenticates can only
            # ever see this one.
            return _error('too_many_failed_attempts',
                          "too many failed authentication attempts; try again "
                          "later", 429)

        client = request.env['ncollection.api.client'].sudo()._nc_authenticate(
            post.get('client_id'), post.get('client_secret'))
        if not client:
            # 401 with no detail. Distinguishing "unknown client" from "bad
            # secret" would turn this into an enumeration oracle. The throttle
            # counts both identically for the same reason.
            throttle._nc_record_failure(source)
            self._log(route, 401, started)
            return _error('invalid_client', "client authentication failed", 401)

        # Proof of a valid secret. Core clears its login counter on success and
        # this follows that, so a working integration is never a step away from
        # its own lockout.
        throttle._nc_record_success(source)

        if self._rate_limited(client):
            self._log(route, 429, started, client=client)
            return _error('rate_limited', "too many requests", 429)

        requested = (post.get('scope') or '').split()
        try:
            scopes = client._nc_grantable(requested)
        except ValidationError as exc:
            self._log(route, 400, started, client=client)
            return _error('invalid_scope', str(exc), 400)
        if not scopes:
            self._log(route, 400, started, client=client)
            return _error('invalid_scope', "at least one scope is required",
                          400)

        _token, plaintext = request.env['ncollection.api.token'].sudo()._nc_issue(
            client, scopes, TOKEN_TTL_SECONDS)
        self._log(route, 200, started, client=client)
        return _json({
            'access_token': plaintext,
            'token_type': 'Bearer',
            'expires_in': TOKEN_TTL_SECONDS,
            'scope': " ".join(sorted(scopes.mapped('code'))),
        })

    # ---- a resource, to prove the whole chain ----------------------------

    @http.route('%s/contacts' % API_ROOT, type='http', auth='none',
                methods=['GET'], csrf=False, save_session=False,
                readonly=False)
    def contacts(self, limit=None, offset=None, **kwargs):
        """The acceptance criterion's endpoint.

        Reads run as the CLIENT'S USER, never sudo. A client therefore cannot
        read anything the corresponding Odoo user could not read — record rules
        and ACLs apply unchanged. That is Rule 4 (a UI restriction mirrored at
        the ORM) arriving from the other direction: there is no second
        permission model to keep in step.
        """
        started = time.monotonic()
        route = '%s/contacts' % API_ROOT
        token, uid = self._authenticate()
        if not token:
            self._log(route, 401, started)
            return _error('invalid_token', "missing or invalid bearer token",
                          401)

        client = token.client_id
        if self._rate_limited(client):
            self._log(route, 429, started, client=client, scope='contacts:read')
            return _error('rate_limited', "too many requests", 429)

        if not token._nc_has_scope('contacts:read'):
            self._log(route, 403, started, client=client, scope='contacts:read')
            return _error('insufficient_scope',
                          "this token does not carry contacts:read", 403)

        try:
            # Clamped from BOTH ends. `offset` had a floor and `limit` did
            # not, so `?limit=-1` reached Postgres and raised
            # InvalidRowCountInLimitClause — an unhandled 500, which is both a
            # stack trace and a breach of this module's one-error-shape
            # promise. Found in security review.
            limit = min(max(int(limit or 100), 1), 500)
            offset = max(int(offset or 0), 0)
        except (TypeError, ValueError):
            self._log(route, 400, started, client=client)
            return _error('invalid_request',
                          "limit and offset must be integers", 400)

        try:
            partners = request.env['res.partner'].with_user(uid).search_read(
                [], ['id', 'name', 'email', 'phone'],
                limit=limit, offset=offset)
        except AccessError:
            # The acting user cannot read this model. That is a permission
            # answer, not a server fault — a 500 would both leak a stack trace
            # and tell an integrator their request was malformed when it was
            # their access that was wrong.
            self._log(route, 403, started, client=client, scope='contacts:read')
            return _error('access_denied',
                          "the user this client acts as cannot read contacts",
                          403)
        self._log(route, 200, started, client=client, scope='contacts:read')
        return _json({'count': len(partners), 'results': partners})
