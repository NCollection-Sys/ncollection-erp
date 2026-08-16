# -*- coding: utf-8 -*-
"""P8-T02: Customer invoices business endpoints (/api/v1/invoices)."""
import logging
import time

from odoo import http
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.http import request

from .common import API_ROOT, ApiControllerBase, _error, _json

_logger = logging.getLogger(__name__)

INVOICE_READ_FIELDS = [
    'id', 'name', 'partner_id', 'invoice_date', 'state', 'payment_state',
    'amount_untaxed', 'amount_tax', 'amount_total', 'amount_residual'
]
INVOICE_LINE_FIELDS = [
    'id', 'product_id', 'name', 'quantity', 'price_unit', 'price_subtotal'
]


class InvoicesApiController(ApiControllerBase):

    @http.route('%s/invoices' % API_ROOT, type='http', auth='none',
                methods=['GET'], csrf=False, save_session=False,
                readonly=False)
    def list_invoices(self, limit=None, offset=None, partner_id=None,
                      state=None, payment_state=None, **kwargs):
        started = time.monotonic()
        route = '%s/invoices' % API_ROOT
        token, uid, client, refusal = self._require_auth(
            route, started, 'invoices:read')
        if refusal is not None:
            return refusal

        module_err = self._check_model_installed('account.move', 'account')
        if module_err is not None:
            self._log(route, 422, started, client=client, scope='invoices:read')
            return module_err

        limit, offset, refusal = self._parse_pagination(limit, offset)
        if refusal is not None:
            self._log(route, 400, started, client=client, scope='invoices:read')
            return refusal

        domain = [('move_type', 'in', ('out_invoice', 'out_refund'))]
        if partner_id:
            try:
                domain.append(('partner_id', '=', int(partner_id)))
            except ValueError:
                self._log(route, 400, started, client=client, scope='invoices:read')
                return _error('invalid_request', "partner_id must be an integer", 400)
        if state:
            domain.append(('state', '=', state))
        if payment_state:
            domain.append(('payment_state', '=', payment_state))

        try:
            Move = request.env['account.move'].with_user(uid)
            invoices = Move.search_read(
                domain, INVOICE_READ_FIELDS, limit=limit, offset=offset)
        except AccessError:
            self._log(route, 403, started, client=client, scope='invoices:read')
            return _error('access_denied',
                          "the user this client acts as cannot read invoices",
                          403)

        self._log(route, 200, started, client=client, scope='invoices:read')
        return _json({'count': len(invoices), 'results': invoices})

    @http.route('%s/invoices' % API_ROOT, type='http', auth='none',
                methods=['POST'], csrf=False, save_session=False,
                readonly=False)
    def create_invoice(self, **kwargs):
        started = time.monotonic()
        route = '%s/invoices' % API_ROOT
        token, uid, client, refusal = self._require_auth(
            route, started, 'invoices:write')
        if refusal is not None:
            return refusal

        module_err = self._check_model_installed('account.move', 'account')
        if module_err is not None:
            self._log(route, 422, started, client=client, scope='invoices:write')
            return module_err

        body, refusal = self._parse_json()
        if refusal is not None:
            self._log(route, 400, started, client=client, scope='invoices:write')
            return refusal

        partner_id = body.get('partner_id')
        if not partner_id:
            self._log(route, 400, started, client=client, scope='invoices:write')
            return _error('validation_error', "field 'partner_id' is required", 400)

        lines_data = body.get('invoice_line_ids', [])
        command_lines = []
        for line in lines_data:
            line_vals = {
                'quantity': float(line.get('quantity', 1.0)),
                'price_unit': float(line.get('price_unit', 0.0)),
            }
            if 'product_id' in line:
                line_vals['product_id'] = int(line['product_id'])
            if 'name' in line:
                line_vals['name'] = line['name']
            elif not line_vals.get('product_id'):
                line_vals['name'] = 'Invoice Line'
            command_lines.append((0, 0, line_vals))

        create_vals = {
            'move_type': 'out_invoice',
            'partner_id': int(partner_id),
            'invoice_date': body.get('invoice_date') or False,
            'invoice_line_ids': command_lines,
        }

        try:
            Move = request.env['account.move'].with_user(uid)
            invoice = Move.create(create_vals)
            res = invoice.read(INVOICE_READ_FIELDS)[0]
            res['invoice_line_ids'] = invoice.invoice_line_ids.read(
                INVOICE_LINE_FIELDS)
        except AccessError:
            self._log(route, 403, started, client=client, scope='invoices:write')
            return _error('access_denied',
                          "the user this client acts as cannot create invoices",
                          403)
        except (ValidationError, UserError, ValueError) as exc:
            self._log(route, 400, started, client=client, scope='invoices:write')
            return _error('validation_error', str(exc), 400)

        self._log(route, 201, started, client=client, scope='invoices:write')
        try:
            request.env['ncollection.webhook.dispatcher'].dispatch_event('invoice.created', res)
        except Exception as exc:
            _logger.debug("api: webhook dispatch error on invoice create: %s", exc)
        return _json(res, status=201)

    @http.route('%s/invoices/<int:move_id>' % API_ROOT, type='http',
                auth='none', methods=['GET'], csrf=False, save_session=False,
                readonly=False)
    def get_invoice(self, move_id, **kwargs):
        started = time.monotonic()
        route = '%s/invoices/%d' % (API_ROOT, move_id)
        token, uid, client, refusal = self._require_auth(
            route, started, 'invoices:read')
        if refusal is not None:
            return refusal

        module_err = self._check_model_installed('account.move', 'account')
        if module_err is not None:
            self._log(route, 422, started, client=client, scope='invoices:read')
            return module_err

        try:
            invoice = request.env['account.move'].with_user(uid).browse(move_id)
            if not invoice.exists() or invoice.move_type not in (
                    'out_invoice', 'out_refund'):
                self._log(route, 404, started, client=client, scope='invoices:read')
                return _error('not_found', "invoice %d not found" % move_id, 404)
            res = invoice.read(INVOICE_READ_FIELDS)[0]
            res['invoice_line_ids'] = invoice.invoice_line_ids.read(
                INVOICE_LINE_FIELDS)
        except AccessError:
            self._log(route, 403, started, client=client, scope='invoices:read')
            return _error('access_denied',
                          "the user this client acts as cannot read invoices",
                          403)

        self._log(route, 200, started, client=client, scope='invoices:read')
        return _json(res)

    @http.route('%s/invoices/<int:move_id>/action_post' % API_ROOT,
                type='http', auth='none', methods=['POST'], csrf=False,
                save_session=False, readonly=False)
    def post_invoice(self, move_id, **kwargs):
        started = time.monotonic()
        route = '%s/invoices/%d/action_post' % (API_ROOT, move_id)
        token, uid, client, refusal = self._require_auth(
            route, started, 'invoices:write')
        if refusal is not None:
            return refusal

        module_err = self._check_model_installed('account.move', 'account')
        if module_err is not None:
            self._log(route, 422, started, client=client, scope='invoices:write')
            return module_err

        try:
            invoice = request.env['account.move'].with_user(uid).browse(move_id)
            if not invoice.exists() or invoice.move_type not in (
                    'out_invoice', 'out_refund'):
                self._log(route, 404, started, client=client, scope='invoices:write')
                return _error('not_found', "invoice %d not found" % move_id, 404)
            invoice.action_post()
            res = invoice.read(INVOICE_READ_FIELDS)[0]
        except AccessError:
            self._log(route, 403, started, client=client, scope='invoices:write')
            return _error('access_denied',
                          "the user this client acts as cannot post invoices",
                          403)
        except (ValidationError, UserError) as exc:
            self._log(route, 400, started, client=client, scope='invoices:write')
            return _error('validation_error', str(exc), 400)

        self._log(route, 200, started, client=client, scope='invoices:write')
        try:
            request.env['ncollection.webhook.dispatcher'].dispatch_event('invoice.posted', res)
        except Exception as exc:
            _logger.debug("api: webhook dispatch error on invoice post: %s", exc)
        return _json(res)
