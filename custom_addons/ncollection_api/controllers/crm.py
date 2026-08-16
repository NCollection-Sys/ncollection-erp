# -*- coding: utf-8 -*-
"""P8-T02: CRM leads business endpoints (/api/v1/crm/leads)."""
import logging
import time

from odoo import http
from odoo.exceptions import AccessError, ValidationError
from odoo.http import request

from .common import API_ROOT, ApiControllerBase, _error, _json

_logger = logging.getLogger(__name__)

CRM_LEAD_FIELDS = [
    'id', 'name', 'partner_id', 'stage_id', 'expected_revenue', 'email_from',
    'phone', 'type', 'active'
]


class CrmApiController(ApiControllerBase):

    @http.route('%s/crm/leads' % API_ROOT, type='http', auth='none',
                methods=['GET'], csrf=False, save_session=False,
                readonly=False)
    def list_leads(self, limit=None, offset=None, stage_id=None,
                   partner_id=None, type=None, **kwargs):
        started = time.monotonic()
        route = '%s/crm/leads' % API_ROOT
        token, uid, client, refusal = self._require_auth(
            route, started, 'crm:read')
        if refusal is not None:
            return refusal

        module_err = self._check_model_installed('crm.lead', 'crm')
        if module_err is not None:
            self._log(route, 422, started, client=client, scope='crm:read')
            return module_err

        limit, offset, refusal = self._parse_pagination(limit, offset)
        if refusal is not None:
            self._log(route, 400, started, client=client, scope='crm:read')
            return refusal

        domain = []
        if stage_id:
            try:
                domain.append(('stage_id', '=', int(stage_id)))
            except ValueError:
                self._log(route, 400, started, client=client, scope='crm:read')
                return _error('invalid_request', "stage_id must be an integer", 400)
        if partner_id:
            try:
                domain.append(('partner_id', '=', int(partner_id)))
            except ValueError:
                self._log(route, 400, started, client=client, scope='crm:read')
                return _error('invalid_request', "partner_id must be an integer", 400)
        if type:
            domain.append(('type', '=', type))

        try:
            Lead = request.env['crm.lead'].with_user(uid)
            leads = Lead.search_read(
                domain, CRM_LEAD_FIELDS, limit=limit, offset=offset)
        except AccessError:
            self._log(route, 403, started, client=client, scope='crm:read')
            return _error('access_denied',
                          "the user this client acts as cannot read CRM leads",
                          403)

        self._log(route, 200, started, client=client, scope='crm:read')
        return _json({'count': len(leads), 'results': leads})

    @http.route('%s/crm/leads' % API_ROOT, type='http', auth='none',
                methods=['POST'], csrf=False, save_session=False,
                readonly=False)
    def create_lead(self, **kwargs):
        started = time.monotonic()
        route = '%s/crm/leads' % API_ROOT
        token, uid, client, refusal = self._require_auth(
            route, started, 'crm:write')
        if refusal is not None:
            return refusal

        module_err = self._check_model_installed('crm.lead', 'crm')
        if module_err is not None:
            self._log(route, 422, started, client=client, scope='crm:write')
            return module_err

        body, refusal = self._parse_json()
        if refusal is not None:
            self._log(route, 400, started, client=client, scope='crm:write')
            return refusal

        name = body.get('name')
        if not name or not str(name).strip():
            self._log(route, 400, started, client=client, scope='crm:write')
            return _error('validation_error', "field 'name' is required", 400)

        create_vals = {
            'name': str(name).strip(),
            'partner_id': int(body['partner_id']) if body.get('partner_id') else False,
            'expected_revenue': float(body.get('expected_revenue', 0.0)),
            'email_from': body.get('email_from') or False,
            'phone': body.get('phone') or False,
            'description': body.get('description') or False,
            'type': body.get('type') or 'opportunity',
        }

        try:
            Lead = request.env['crm.lead'].with_user(uid)
            lead = Lead.create(create_vals)
            res = lead.read(CRM_LEAD_FIELDS)[0]
        except AccessError:
            self._log(route, 403, started, client=client, scope='crm:write')
            return _error('access_denied',
                          "the user this client acts as cannot create CRM leads",
                          403)
        except (ValidationError, ValueError) as exc:
            self._log(route, 400, started, client=client, scope='crm:write')
            return _error('validation_error', str(exc), 400)

        self._log(route, 201, started, client=client, scope='crm:write')
        try:
            request.env['ncollection.webhook.dispatcher'].dispatch_event('crm.lead.created', res)
        except Exception as exc:
            _logger.debug("api: webhook dispatch error on crm lead create: %s", exc)
        return _json(res, status=201)

    @http.route('%s/crm/leads/<int:lead_id>' % API_ROOT, type='http',
                auth='none', methods=['GET'], csrf=False, save_session=False,
                readonly=False)
    def get_lead(self, lead_id, **kwargs):
        started = time.monotonic()
        route = '%s/crm/leads/%d' % (API_ROOT, lead_id)
        token, uid, client, refusal = self._require_auth(
            route, started, 'crm:read')
        if refusal is not None:
            return refusal

        module_err = self._check_model_installed('crm.lead', 'crm')
        if module_err is not None:
            self._log(route, 422, started, client=client, scope='crm:read')
            return module_err

        try:
            lead = request.env['crm.lead'].with_user(uid).browse(lead_id)
            if not lead.exists():
                self._log(route, 404, started, client=client, scope='crm:read')
                return _error('not_found', "lead %d not found" % lead_id, 404)
            res = lead.read(CRM_LEAD_FIELDS)[0]
        except AccessError:
            self._log(route, 403, started, client=client, scope='crm:read')
            return _error('access_denied',
                          "the user this client acts as cannot read CRM leads",
                          403)

        self._log(route, 200, started, client=client, scope='crm:read')
        return _json(res)

    @http.route('%s/crm/leads/<int:lead_id>' % API_ROOT, type='http',
                auth='none', methods=['PUT'], csrf=False, save_session=False,
                readonly=False)
    def update_lead(self, lead_id, **kwargs):
        started = time.monotonic()
        route = '%s/crm/leads/%d' % (API_ROOT, lead_id)
        token, uid, client, refusal = self._require_auth(
            route, started, 'crm:write')
        if refusal is not None:
            return refusal

        module_err = self._check_model_installed('crm.lead', 'crm')
        if module_err is not None:
            self._log(route, 422, started, client=client, scope='crm:write')
            return module_err

        body, refusal = self._parse_json()
        if refusal is not None:
            self._log(route, 400, started, client=client, scope='crm:write')
            return refusal

        try:
            lead = request.env['crm.lead'].with_user(uid).browse(lead_id)
            if not lead.exists():
                self._log(route, 404, started, client=client, scope='crm:write')
                return _error('not_found', "lead %d not found" % lead_id, 404)

            update_vals = {}
            for field in ('name', 'email_from', 'phone', 'description', 'type'):
                if field in body:
                    update_vals[field] = body[field]
            if 'partner_id' in body:
                update_vals['partner_id'] = int(body['partner_id']) if body['partner_id'] else False
            if 'stage_id' in body:
                update_vals['stage_id'] = int(body['stage_id']) if body['stage_id'] else False
            if 'expected_revenue' in body:
                update_vals['expected_revenue'] = float(body['expected_revenue'])

            if update_vals:
                lead.write(update_vals)
            res = lead.read(CRM_LEAD_FIELDS)[0]
        except AccessError:
            self._log(route, 403, started, client=client, scope='crm:write')
            return _error('access_denied',
                          "the user this client acts as cannot update CRM leads",
                          403)
        except (ValidationError, ValueError) as exc:
            self._log(route, 400, started, client=client, scope='crm:write')
            return _error('validation_error', str(exc), 400)

        self._log(route, 200, started, client=client, scope='crm:write')
        return _json(res)
