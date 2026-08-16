# -*- coding: utf-8 -*-
"""P8-T03: Webhooks REST API endpoints for subscriptions and delivery logs."""
import logging
import time

from odoo import http
from odoo.exceptions import AccessError, ValidationError
from odoo.http import request

from .common import API_ROOT, ApiControllerBase, _error, _json

_logger = logging.getLogger(__name__)

SUBSCRIPTION_READ_FIELDS = [
    'id', 'name', 'client_id', 'target_url', 'secret',
    'active', 'event_types', 'state', 'failure_count', 'create_date',
]

DELIVERY_READ_FIELDS = [
    'id', 'uuid', 'subscription_id', 'event', 'payload',
    'state', 'attempt', 'max_attempts', 'next_retry',
    'response_code', 'response_body', 'duration_ms',
    'error_message', 'delivered_at', 'create_date',
]


class WebhookApiController(ApiControllerBase):
    """REST API endpoints for webhook subscriptions and delivery logs (P8-T03)."""

    # ---- Webhook Subscriptions ----

    @http.route('%s/webhooks/subscriptions' % API_ROOT, type='http', auth='none',
                methods=['GET'], csrf=False, save_session=False, readonly=False)
    def list_subscriptions(self, limit=None, offset=None, state=None, **kwargs):
        """List webhook subscriptions with pagination."""
        started = time.monotonic()
        route = '%s/webhooks/subscriptions' % API_ROOT
        token, uid, client, refusal = self._require_auth(
            route, started, 'webhooks:read')
        if refusal is not None:
            return refusal

        limit, offset, refusal = self._parse_pagination(limit, offset)
        if refusal is not None:
            self._log(route, 400, started, client=client, scope='webhooks:read')
            return refusal

        domain = []
        if state:
            domain.append(('state', '=', state))

        try:
            Sub = request.env['ncollection.webhook.subscription'].with_user(uid)
            total = Sub.search_count(domain)
            records = Sub.search_read(
                domain, SUBSCRIPTION_READ_FIELDS, limit=limit, offset=offset, order='id asc')
        except AccessError:
            self._log(route, 403, started, client=client, scope='webhooks:read')
            return _error('access_denied',
                          "the user this client acts as cannot read webhook subscriptions",
                          403)

        self._log(route, 200, started, client=client, scope='webhooks:read')
        return _json({
            'total': total,
            'count': len(records),
            'limit': limit,
            'offset': offset,
            'results': records,
        })

    @http.route('%s/webhooks/subscriptions' % API_ROOT, type='http', auth='none',
                methods=['POST'], csrf=False, save_session=False, readonly=False)
    def create_subscription(self, **kwargs):
        """Register a new webhook subscription."""
        started = time.monotonic()
        route = '%s/webhooks/subscriptions' % API_ROOT
        token, uid, client, refusal = self._require_auth(
            route, started, 'webhooks:write')
        if refusal is not None:
            return refusal

        body, refusal = self._parse_json()
        if refusal is not None:
            self._log(route, 400, started, client=client, scope='webhooks:write')
            return refusal

        name = body.get('name')
        target_url = body.get('target_url')
        if not name or not str(name).strip() or not target_url or not str(target_url).strip():
            self._log(route, 400, started, client=client, scope='webhooks:write')
            return _error('missing_required_field', "Fields 'name' and 'target_url' are required.", 400)

        vals = {
            'name': str(name).strip(),
            'target_url': str(target_url).strip(),
            'client_id': client.id if client else False,
            'event_types': body.get('event_types', '*'),
            'active': body.get('active', True),
        }
        if body.get('secret'):
            vals['secret'] = body['secret']

        try:
            Sub = request.env['ncollection.webhook.subscription'].with_user(uid)
            rec = Sub.create(vals)
            res = rec.read(SUBSCRIPTION_READ_FIELDS)[0]
        except AccessError:
            self._log(route, 403, started, client=client, scope='webhooks:write')
            return _error('access_denied',
                          "the user this client acts as cannot create webhook subscriptions",
                          403)
        except (ValidationError, ValueError) as exc:
            self._log(route, 400, started, client=client, scope='webhooks:write')
            return _error('validation_error', str(exc), 400)

        self._log(route, 201, started, client=client, scope='webhooks:write')
        return _json(res, status=201)

    @http.route('%s/webhooks/subscriptions/<int:sub_id>' % API_ROOT, type='http', auth='none',
                methods=['GET'], csrf=False, save_session=False, readonly=False)
    def get_subscription(self, sub_id, **kwargs):
        """Retrieve details of a single webhook subscription."""
        started = time.monotonic()
        route = '%s/webhooks/subscriptions/%d' % (API_ROOT, sub_id)
        token, uid, client, refusal = self._require_auth(
            route, started, 'webhooks:read')
        if refusal is not None:
            return refusal

        try:
            Sub = request.env['ncollection.webhook.subscription'].with_user(uid)
            rec = Sub.browse(sub_id)
            if not rec.exists():
                self._log(route, 404, started, client=client, scope='webhooks:read')
                return _error('not_found', "webhook subscription %d not found" % sub_id, 404)
            res = rec.read(SUBSCRIPTION_READ_FIELDS)[0]
        except AccessError:
            self._log(route, 403, started, client=client, scope='webhooks:read')
            return _error('access_denied',
                          "the user this client acts as cannot read webhook subscriptions",
                          403)

        self._log(route, 200, started, client=client, scope='webhooks:read')
        return _json(res)

    @http.route('%s/webhooks/subscriptions/<int:sub_id>' % API_ROOT, type='http', auth='none',
                methods=['PUT'], csrf=False, save_session=False, readonly=False)
    def update_subscription(self, sub_id, **kwargs):
        """Update an existing webhook subscription."""
        started = time.monotonic()
        route = '%s/webhooks/subscriptions/%d' % (API_ROOT, sub_id)
        token, uid, client, refusal = self._require_auth(
            route, started, 'webhooks:write')
        if refusal is not None:
            return refusal

        body, refusal = self._parse_json()
        if refusal is not None:
            self._log(route, 400, started, client=client, scope='webhooks:write')
            return refusal

        allowed_fields = {'name', 'target_url', 'event_types', 'active', 'state', 'secret'}
        vals = {k: v for k, v in body.items() if k in allowed_fields}

        try:
            Sub = request.env['ncollection.webhook.subscription'].with_user(uid)
            rec = Sub.browse(sub_id)
            if not rec.exists():
                self._log(route, 404, started, client=client, scope='webhooks:write')
                return _error('not_found', "webhook subscription %d not found" % sub_id, 404)
            rec.write(vals)
            res = rec.read(SUBSCRIPTION_READ_FIELDS)[0]
        except AccessError:
            self._log(route, 403, started, client=client, scope='webhooks:write')
            return _error('access_denied',
                          "the user this client acts as cannot update webhook subscriptions",
                          403)
        except (ValidationError, ValueError) as exc:
            self._log(route, 400, started, client=client, scope='webhooks:write')
            return _error('validation_error', str(exc), 400)

        self._log(route, 200, started, client=client, scope='webhooks:write')
        return _json(res)

    @http.route('%s/webhooks/subscriptions/<int:sub_id>' % API_ROOT, type='http', auth='none',
                methods=['DELETE'], csrf=False, save_session=False, readonly=False)
    def delete_subscription(self, sub_id, **kwargs):
        """Delete a webhook subscription."""
        started = time.monotonic()
        route = '%s/webhooks/subscriptions/%d' % (API_ROOT, sub_id)
        token, uid, client, refusal = self._require_auth(
            route, started, 'webhooks:write')
        if refusal is not None:
            return refusal

        try:
            Sub = request.env['ncollection.webhook.subscription'].with_user(uid)
            rec = Sub.browse(sub_id)
            if not rec.exists():
                self._log(route, 404, started, client=client, scope='webhooks:write')
                return _error('not_found', "webhook subscription %d not found" % sub_id, 404)
            rec.unlink()
        except AccessError:
            self._log(route, 403, started, client=client, scope='webhooks:write')
            return _error('access_denied',
                          "the user this client acts as cannot delete webhook subscriptions",
                          403)

        self._log(route, 200, started, client=client, scope='webhooks:write')
        return _json({'success': True, 'id': sub_id})

    # ---- Webhook Delivery Logs ----

    @http.route('%s/webhooks/deliveries' % API_ROOT, type='http', auth='none',
                methods=['GET'], csrf=False, save_session=False, readonly=False)
    def list_deliveries(self, limit=None, offset=None, subscription_id=None,
                        state=None, event=None, **kwargs):
        """List webhook delivery attempt records."""
        started = time.monotonic()
        route = '%s/webhooks/deliveries' % API_ROOT
        token, uid, client, refusal = self._require_auth(
            route, started, 'webhooks:read')
        if refusal is not None:
            return refusal

        limit, offset, refusal = self._parse_pagination(limit, offset)
        if refusal is not None:
            self._log(route, 400, started, client=client, scope='webhooks:read')
            return refusal

        domain = []
        if subscription_id:
            try:
                domain.append(('subscription_id', '=', int(subscription_id)))
            except (TypeError, ValueError):
                self._log(route, 400, started, client=client, scope='webhooks:read')
                return _error('invalid_request', "Parameter 'subscription_id' must be an integer.", 400)

        if state:
            domain.append(('state', '=', state))
        if event:
            domain.append(('event', '=', event))

        try:
            Delivery = request.env['ncollection.webhook.delivery'].with_user(uid)
            total = Delivery.search_count(domain)
            records = Delivery.search_read(
                domain, DELIVERY_READ_FIELDS, limit=limit, offset=offset, order='id desc')
        except AccessError:
            self._log(route, 403, started, client=client, scope='webhooks:read')
            return _error('access_denied',
                          "the user this client acts as cannot read webhook deliveries",
                          403)

        self._log(route, 200, started, client=client, scope='webhooks:read')
        return _json({
            'total': total,
            'count': len(records),
            'limit': limit,
            'offset': offset,
            'results': records,
        })

    @http.route('%s/webhooks/deliveries/<int:delivery_id>/action_retry' % API_ROOT, type='http', auth='none',
                methods=['POST'], csrf=False, save_session=False, readonly=False)
    def retry_delivery(self, delivery_id, **kwargs):
        """Trigger an immediate retry for a failed or dead-letter delivery."""
        started = time.monotonic()
        route = '%s/webhooks/deliveries/%d/action_retry' % (API_ROOT, delivery_id)
        token, uid, client, refusal = self._require_auth(
            route, started, 'webhooks:write')
        if refusal is not None:
            return refusal

        try:
            Delivery = request.env['ncollection.webhook.delivery'].with_user(uid)
            rec = Delivery.browse(delivery_id)
            if not rec.exists():
                self._log(route, 404, started, client=client, scope='webhooks:write')
                return _error('not_found', "webhook delivery %d not found" % delivery_id, 404)
            rec.action_retry()
            res = rec.read(DELIVERY_READ_FIELDS)[0]
        except AccessError:
            self._log(route, 403, started, client=client, scope='webhooks:write')
            return _error('access_denied',
                          "the user this client acts as cannot retry webhook deliveries",
                          403)
        except Exception as exc:
            self._log(route, 400, started, client=client, scope='webhooks:write')
            return _error('retry_failed', str(exc), 400)

        self._log(route, 200, started, client=client, scope='webhooks:write')
        return _json(res)
