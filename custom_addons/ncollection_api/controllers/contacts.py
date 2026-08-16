# -*- coding: utf-8 -*-
"""P8-T02: Contacts business endpoints (/api/v1/contacts)."""
import logging
import time

from odoo import http
from odoo.exceptions import AccessError, ValidationError
from odoo.http import request

from .common import API_ROOT, ApiControllerBase, _error, _json

_logger = logging.getLogger(__name__)

CONTACT_READ_FIELDS = [
    'id', 'name', 'email', 'phone', 'is_company', 'street', 'city', 'vat',
    'active'
]


class ContactsApiController(ApiControllerBase):

    @http.route('%s/contacts' % API_ROOT, type='http', auth='none',
                methods=['GET'], csrf=False, save_session=False,
                readonly=False)
    def list_contacts(self, limit=None, offset=None, name=None, email=None,
                      is_company=None, **kwargs):
        started = time.monotonic()
        route = '%s/contacts' % API_ROOT
        token, uid, client, refusal = self._require_auth(
            route, started, 'contacts:read')
        if refusal is not None:
            return refusal

        limit, offset, refusal = self._parse_pagination(limit, offset)
        if refusal is not None:
            self._log(route, 400, started, client=client, scope='contacts:read')
            return refusal

        domain = []
        if name:
            domain.append(('name', 'ilike', name))
        if email:
            domain.append(('email', 'ilike', email))
        if is_company is not None:
            domain.append(('is_company', '=', str(is_company).lower() in ('1', 'true')))

        try:
            Partner = request.env['res.partner'].with_user(uid)
            partners = Partner.search_read(
                domain, CONTACT_READ_FIELDS, limit=limit, offset=offset)
        except AccessError:
            self._log(route, 403, started, client=client, scope='contacts:read')
            return _error('access_denied',
                          "the user this client acts as cannot read contacts",
                          403)

        self._log(route, 200, started, client=client, scope='contacts:read')
        return _json({'count': len(partners), 'results': partners})

    @http.route('%s/contacts' % API_ROOT, type='http', auth='none',
                methods=['POST'], csrf=False, save_session=False,
                readonly=False)
    def create_contact(self, **kwargs):
        started = time.monotonic()
        route = '%s/contacts' % API_ROOT
        token, uid, client, refusal = self._require_auth(
            route, started, 'contacts:write')
        if refusal is not None:
            return refusal

        body, refusal = self._parse_json()
        if refusal is not None:
            self._log(route, 400, started, client=client, scope='contacts:write')
            return refusal

        name = body.get('name')
        if not name or not str(name).strip():
            self._log(route, 400, started, client=client, scope='contacts:write')
            return _error('validation_error', "field 'name' is required", 400)

        create_vals = {
            'name': str(name).strip(),
            'email': body.get('email') or False,
            'phone': body.get('phone') or False,
            'is_company': bool(body.get('is_company', False)),
            'street': body.get('street') or False,
            'city': body.get('city') or False,
            'vat': body.get('vat') or False,
        }

        try:
            Partner = request.env['res.partner'].with_user(uid)
            partner = Partner.create(create_vals)
            res = partner.read(CONTACT_READ_FIELDS)[0]
        except AccessError:
            self._log(route, 403, started, client=client, scope='contacts:write')
            return _error('access_denied',
                          "the user this client acts as cannot create contacts",
                          403)
        except ValidationError as exc:
            self._log(route, 400, started, client=client, scope='contacts:write')
            return _error('validation_error', str(exc), 400)

        self._log(route, 201, started, client=client, scope='contacts:write')
        try:
            request.env['ncollection.webhook.dispatcher'].dispatch_event('contact.created', res)
        except Exception as exc:
            _logger.debug("api: webhook dispatch error on contact create: %s", exc)
        return _json(res, status=201)

    @http.route('%s/contacts/<int:partner_id>' % API_ROOT, type='http',
                auth='none', methods=['GET'], csrf=False, save_session=False,
                readonly=False)
    def get_contact(self, partner_id, **kwargs):
        started = time.monotonic()
        route = '%s/contacts/%d' % (API_ROOT, partner_id)
        token, uid, client, refusal = self._require_auth(
            route, started, 'contacts:read')
        if refusal is not None:
            return refusal

        try:
            partner = request.env['res.partner'].with_user(uid).browse(partner_id)
            if not partner.exists():
                self._log(route, 404, started, client=client, scope='contacts:read')
                return _error('not_found', "contact %d not found" % partner_id, 404)
            res = partner.read(CONTACT_READ_FIELDS)[0]
        except AccessError:
            self._log(route, 403, started, client=client, scope='contacts:read')
            return _error('access_denied',
                          "the user this client acts as cannot read contacts",
                          403)

        self._log(route, 200, started, client=client, scope='contacts:read')
        return _json(res)

    @http.route('%s/contacts/<int:partner_id>' % API_ROOT, type='http',
                auth='none', methods=['PUT'], csrf=False, save_session=False,
                readonly=False)
    def update_contact(self, partner_id, **kwargs):
        started = time.monotonic()
        route = '%s/contacts/%d' % (API_ROOT, partner_id)
        token, uid, client, refusal = self._require_auth(
            route, started, 'contacts:write')
        if refusal is not None:
            return refusal

        body, refusal = self._parse_json()
        if refusal is not None:
            self._log(route, 400, started, client=client, scope='contacts:write')
            return refusal

        try:
            partner = request.env['res.partner'].with_user(uid).browse(partner_id)
            if not partner.exists():
                self._log(route, 404, started, client=client, scope='contacts:write')
                return _error('not_found', "contact %d not found" % partner_id, 404)

            update_vals = {}
            for field in ('name', 'email', 'phone', 'is_company', 'street',
                          'city', 'vat'):
                if field in body:
                    update_vals[field] = body[field]

            if update_vals:
                partner.write(update_vals)
            res = partner.read(CONTACT_READ_FIELDS)[0]
        except AccessError:
            self._log(route, 403, started, client=client, scope='contacts:write')
            return _error('access_denied',
                          "the user this client acts as cannot update contacts",
                          403)
        except ValidationError as exc:
            self._log(route, 400, started, client=client, scope='contacts:write')
            return _error('validation_error', str(exc), 400)

        self._log(route, 200, started, client=client, scope='contacts:write')
        return _json(res)

    @http.route('%s/contacts/<int:partner_id>' % API_ROOT, type='http',
                auth='none', methods=['DELETE'], csrf=False, save_session=False,
                readonly=False)
    def delete_contact(self, partner_id, **kwargs):
        started = time.monotonic()
        route = '%s/contacts/%d' % (API_ROOT, partner_id)
        token, uid, client, refusal = self._require_auth(
            route, started, 'contacts:write')
        if refusal is not None:
            return refusal

        try:
            partner = request.env['res.partner'].with_user(uid).browse(partner_id)
            if not partner.exists():
                self._log(route, 404, started, client=client, scope='contacts:write')
                return _error('not_found', "contact %d not found" % partner_id, 404)
            # Archive contact rather than hard-deleting to preserve FK integrity
            partner.write({'active': False})
        except AccessError:
            self._log(route, 403, started, client=client, scope='contacts:write')
            return _error('access_denied',
                          "the user this client acts as cannot delete contacts",
                          403)

        self._log(route, 200, started, client=client, scope='contacts:write')
        return _json({'success': True, 'message': "contact archived"})
