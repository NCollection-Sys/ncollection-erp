# -*- coding: utf-8 -*-
"""P8-T02: Shared REST API controller base, helpers, error envelopes, and guards."""
import json
import logging
import time

from odoo import http
from odoo.http import Response, request

_logger = logging.getLogger(__name__)

API_ROOT = '/api/v1'
TOKEN_TTL_SECONDS = 3600


def _json(payload, status=200):
    return Response(json.dumps(payload, default=str), status=status,
                    content_type='application/json; charset=utf-8')


def _error(code, message, status):
    """The ONE error shape. Every failure path goes through here."""
    return _json({'error': {'code': code, 'message': message}}, status=status)


class ApiControllerBase(http.Controller):
    """Base controller providing auth, rate-limiting, logging, and error handling."""

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
            _logger.exception("api: could not write request log for %s", route)

    def _rate_limited(self, client):
        """True when this client is over its own per-minute allowance."""
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

    def _require_auth(self, route, started, scope):
        """Authenticate token, rate limit, and verify scope.

        Returns ``(token, uid, client, None)`` on success,
        or ``(None, None, None, Response)`` on refusal.
        """
        token, uid = self._authenticate()
        if not token:
            self._log(route, 401, started)
            return None, None, None, _error(
                'invalid_token', "missing or invalid bearer token", 401)

        client = token.client_id
        if self._rate_limited(client):
            self._log(route, 429, started, client=client, scope=scope)
            return None, None, None, _error(
                'rate_limited', "too many requests", 429)

        if not token._nc_has_scope(scope):
            self._log(route, 403, started, client=client, scope=scope)
            return None, None, None, _error(
                'insufficient_scope',
                "this token does not carry %s" % scope, 403)

        return token, uid, client, None

    def _parse_json(self):
        """Extract and parse JSON body.

        Returns ``(data, None)`` on success, or ``(None, Response)`` on malformed body.
        """
        raw_data = request.httprequest.data
        if not raw_data:
            return {}, None
        try:
            parsed = json.loads(raw_data.decode('utf-8'))
            if not isinstance(parsed, dict):
                return None, _error('invalid_request',
                                    "JSON body must be an object", 400)
            return parsed, None
        except (ValueError, UnicodeDecodeError):
            return None, _error('invalid_request',
                                "malformed JSON in request body", 400)

    def _parse_pagination(self, limit=None, offset=None, default_limit=50,
                          max_limit=200):
        """Safely parse and clamp limit and offset.

        Returns ``(limit, offset, None)`` or ``(None, None, Response)``.
        """
        try:
            parsed_limit = min(max(int(limit or default_limit), 1), max_limit)
            parsed_offset = max(int(offset or 0), 0)
            return parsed_limit, parsed_offset, None
        except (TypeError, ValueError):
            return None, None, _error('invalid_request',
                                      "limit and offset must be integers", 400)

    def _check_model_installed(self, model_name, feature_name):
        """Verify model exists in registry for the current database."""
        if model_name not in request.env:
            return _error(
                'module_not_installed',
                "The %s module is not installed for this workspace" % feature_name,
                422)
        return None
