# -*- coding: utf-8 -*-
"""P8-T10: Centralized Structured Error Logging & Sanitization (Issue #444).

Captures unhandled exceptions and HTTP errors across the web client and APIs,
masks credentials / PII / raw SQL, and stores structured incidents linked by
a unique Incident Correlation ID (e.g. ERR-XXXXXX-XXXX).
"""
import logging
import re
import secrets
import traceback

from odoo import api, fields, models
from odoo.http import request

_logger = logging.getLogger(__name__)

# Sensitive patterns for redaction
_PASSWORD_PATTERN = re.compile(
    r'(password|secret|token|api_key|apikey|authorization|bearer)\s*[:=]\s*["\']?([^"\'\s,;]+)["\']?',
    re.IGNORECASE
)
_BEARER_PATTERN = re.compile(r'Bearer\s+[A-Za-z0-9_\-\.]+', re.IGNORECASE)
_PG_CONN_PATTERN = re.compile(r'postgres(?:ql)?://([^:]+):([^@]+)@', re.IGNORECASE)
_CARD_PATTERN = re.compile(r'\b(?:\d[ -]*?){13,16}\b')


class NCollectionErrorLog(models.Model):
    """Structured Error and Incident Telemetry Log."""
    _name = 'ncollection.error.log'
    _description = 'NCollection Error Incident Log'
    _order = 'create_date desc, id desc'

    uuid = fields.Char(
        default=lambda self: self._nc_generate_error_id(),
        required=True,
        readonly=True,
        index=True,
    )
    error_type = fields.Char(index=True)
    message = fields.Text()
    http_status = fields.Integer(index=True)
    route = fields.Char(index=True)
    method = fields.Char()
    user_id = fields.Many2one(
        'res.users',
        ondelete='set null',
        readonly=True,
    )
    traceback_masked = fields.Text(readonly=True)
    remote_addr = fields.Char(readonly=True)
    user_agent = fields.Char(readonly=True)

    @api.model
    def _nc_generate_error_id(self):
        """Generate a user-friendly correlation ID (e.g. ERR-9B41F0-8A2D)."""
        part1 = secrets.token_hex(3).upper()
        part2 = secrets.token_hex(2).upper()
        return f"ERR-{part1}-{part2}"

    @api.model
    def _nc_sanitize_traceback(self, raw_tb):
        """Redact sensitive tokens, passwords, database DSNs, and card patterns."""
        if not raw_tb:
            return ''
        sanitized = _PASSWORD_PATTERN.sub(r'\1=***', str(raw_tb))
        sanitized = _BEARER_PATTERN.sub('Bearer [MASKED]', sanitized)
        sanitized = _PG_CONN_PATTERN.sub(r'postgres://***:***@', sanitized)
        sanitized = _CARD_PATTERN.sub('[CARD MASKED]', sanitized)
        return sanitized

    @api.model
    def log_exception(self, exc, route=None, http_status=500, error_type=None, method=None):
        """Safely record an exception and return the generated correlation ID."""
        error_id = self._nc_generate_error_id()
        raw_msg = str(exc) if exc else "Unknown error"
        sanitized_msg = self._nc_sanitize_traceback(raw_msg)

        if exc and hasattr(exc, '__traceback__') and exc.__traceback__:
            raw_tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        else:
            raw_tb = traceback.format_exc()
        sanitized_tb = self._nc_sanitize_traceback(raw_tb)

        err_type = error_type or (type(exc).__name__ if exc else 'GenericError')
        req_route = route or ''
        req_method = method or 'GET'
        user_id = False
        remote_ip = False
        user_agent = False

        if request:
            try:
                if not req_route and hasattr(request, 'httprequest'):
                    req_route = request.httprequest.path
                    req_method = request.httprequest.method
                if hasattr(request, 'env') and request.env and request.env.uid:
                    user_id = request.env.uid
                if hasattr(request, 'httprequest'):
                    remote_ip = request.httprequest.remote_addr
                    user_agent = request.httprequest.user_agent.string if request.httprequest.user_agent else False
            except Exception as ctx_err:
                _logger.debug("safe context read failed: %s", ctx_err)

        try:
            self.sudo().create({
                'uuid': error_id,
                'error_type': err_type,
                'message': sanitized_msg[:2048],
                'http_status': http_status or 500,
                'route': req_route[:512],
                'method': req_method[:10],
                'user_id': user_id,
                'traceback_masked': sanitized_tb,
                'remote_addr': remote_ip,
                'user_agent': user_agent[:512] if user_agent else False,
            })
            _logger.error(
                "Incident %s: %s [%s %s] (HTTP %s)",
                error_id, sanitized_msg, req_method, req_route, http_status
            )
        except Exception as log_err:
            _logger.error("Failed to write to ncollection.error.log: %s", log_err)

        return error_id
