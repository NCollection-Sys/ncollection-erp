# -*- coding: utf-8 -*-
"""P8-T02: Sales orders business endpoints (/api/v1/sales)."""
import time

from odoo import http
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.http import request

from .common import API_ROOT, ApiControllerBase, _error, _json

SALE_READ_FIELDS = [
    'id', 'name', 'partner_id', 'date_order', 'state', 'amount_untaxed',
    'amount_tax', 'amount_total'
]
LINE_READ_FIELDS = [
    'id', 'product_id', 'name', 'product_uom_qty', 'price_unit',
    'price_subtotal'
]


class SalesApiController(ApiControllerBase):

    @http.route('%s/sales' % API_ROOT, type='http', auth='none',
                methods=['GET'], csrf=False, save_session=False,
                readonly=False)
    def list_sales(self, limit=None, offset=None, partner_id=None, state=None,
                   **kwargs):
        started = time.monotonic()
        route = '%s/sales' % API_ROOT
        token, uid, client, refusal = self._require_auth(
            route, started, 'sales:read')
        if refusal is not None:
            return refusal

        module_err = self._check_model_installed('sale.order', 'sale')
        if module_err is not None:
            self._log(route, 422, started, client=client, scope='sales:read')
            return module_err

        limit, offset, refusal = self._parse_pagination(limit, offset)
        if refusal is not None:
            self._log(route, 400, started, client=client, scope='sales:read')
            return refusal

        domain = []
        if partner_id:
            try:
                domain.append(('partner_id', '=', int(partner_id)))
            except ValueError:
                self._log(route, 400, started, client=client, scope='sales:read')
                return _error('invalid_request', "partner_id must be an integer", 400)
        if state:
            domain.append(('state', '=', state))

        try:
            Order = request.env['sale.order'].with_user(uid)
            orders = Order.search_read(
                domain, SALE_READ_FIELDS, limit=limit, offset=offset)
        except AccessError:
            self._log(route, 403, started, client=client, scope='sales:read')
            return _error('access_denied',
                          "the user this client acts as cannot read sales orders",
                          403)

        self._log(route, 200, started, client=client, scope='sales:read')
        return _json({'count': len(orders), 'results': orders})

    @http.route('%s/sales' % API_ROOT, type='http', auth='none',
                methods=['POST'], csrf=False, save_session=False,
                readonly=False)
    def create_sale(self, **kwargs):
        started = time.monotonic()
        route = '%s/sales' % API_ROOT
        token, uid, client, refusal = self._require_auth(
            route, started, 'sales:write')
        if refusal is not None:
            return refusal

        module_err = self._check_model_installed('sale.order', 'sale')
        if module_err is not None:
            self._log(route, 422, started, client=client, scope='sales:write')
            return module_err

        body, refusal = self._parse_json()
        if refusal is not None:
            self._log(route, 400, started, client=client, scope='sales:write')
            return refusal

        partner_id = body.get('partner_id')
        if not partner_id:
            self._log(route, 400, started, client=client, scope='sales:write')
            return _error('validation_error', "field 'partner_id' is required", 400)

        lines_data = body.get('order_line', [])
        command_lines = []
        for line in lines_data:
            product_id = line.get('product_id')
            if not product_id:
                continue
            line_vals = {
                'product_id': int(product_id),
                'product_uom_qty': float(line.get('product_uom_qty', 1.0)),
            }
            if 'price_unit' in line:
                line_vals['price_unit'] = float(line['price_unit'])
            if 'name' in line:
                line_vals['name'] = line['name']
            command_lines.append((0, 0, line_vals))

        create_vals = {
            'partner_id': int(partner_id),
            'order_line': command_lines,
        }

        try:
            Order = request.env['sale.order'].with_user(uid)
            order = Order.create(create_vals)
            res = order.read(SALE_READ_FIELDS)[0]
            res['order_line'] = order.order_line.read(LINE_READ_FIELDS)
        except AccessError:
            self._log(route, 403, started, client=client, scope='sales:write')
            return _error('access_denied',
                          "the user this client acts as cannot create sales orders",
                          403)
        except (ValidationError, UserError, ValueError) as exc:
            self._log(route, 400, started, client=client, scope='sales:write')
            return _error('validation_error', str(exc), 400)

        self._log(route, 201, started, client=client, scope='sales:write')
        return _json(res, status=201)

    @http.route('%s/sales/<int:order_id>' % API_ROOT, type='http',
                auth='none', methods=['GET'], csrf=False, save_session=False,
                readonly=False)
    def get_sale(self, order_id, **kwargs):
        started = time.monotonic()
        route = '%s/sales/%d' % (API_ROOT, order_id)
        token, uid, client, refusal = self._require_auth(
            route, started, 'sales:read')
        if refusal is not None:
            return refusal

        module_err = self._check_model_installed('sale.order', 'sale')
        if module_err is not None:
            self._log(route, 422, started, client=client, scope='sales:read')
            return module_err

        try:
            order = request.env['sale.order'].with_user(uid).browse(order_id)
            if not order.exists():
                self._log(route, 404, started, client=client, scope='sales:read')
                return _error('not_found', "sale order %d not found" % order_id, 404)
            res = order.read(SALE_READ_FIELDS)[0]
            res['order_line'] = order.order_line.read(LINE_READ_FIELDS)
        except AccessError:
            self._log(route, 403, started, client=client, scope='sales:read')
            return _error('access_denied',
                          "the user this client acts as cannot read sales orders",
                          403)

        self._log(route, 200, started, client=client, scope='sales:read')
        return _json(res)

    @http.route('%s/sales/<int:order_id>' % API_ROOT, type='http',
                auth='none', methods=['PUT'], csrf=False, save_session=False,
                readonly=False)
    def update_sale(self, order_id, **kwargs):
        started = time.monotonic()
        route = '%s/sales/%d' % (API_ROOT, order_id)
        token, uid, client, refusal = self._require_auth(
            route, started, 'sales:write')
        if refusal is not None:
            return refusal

        module_err = self._check_model_installed('sale.order', 'sale')
        if module_err is not None:
            self._log(route, 422, started, client=client, scope='sales:write')
            return module_err

        body, refusal = self._parse_json()
        if refusal is not None:
            self._log(route, 400, started, client=client, scope='sales:write')
            return refusal

        try:
            order = request.env['sale.order'].with_user(uid).browse(order_id)
            if not order.exists():
                self._log(route, 404, started, client=client, scope='sales:write')
                return _error('not_found', "sale order %d not found" % order_id, 404)

            update_vals = {}
            if 'partner_id' in body:
                update_vals['partner_id'] = int(body['partner_id'])
            if 'note' in body:
                update_vals['note'] = body['note']

            if update_vals:
                order.write(update_vals)
            res = order.read(SALE_READ_FIELDS)[0]
            res['order_line'] = order.order_line.read(LINE_READ_FIELDS)
        except AccessError:
            self._log(route, 403, started, client=client, scope='sales:write')
            return _error('access_denied',
                          "the user this client acts as cannot update sales orders",
                          403)
        except (ValidationError, UserError, ValueError) as exc:
            self._log(route, 400, started, client=client, scope='sales:write')
            return _error('validation_error', str(exc), 400)

        self._log(route, 200, started, client=client, scope='sales:write')
        return _json(res)

    @http.route('%s/sales/<int:order_id>/action_confirm' % API_ROOT,
                type='http', auth='none', methods=['POST'], csrf=False,
                save_session=False, readonly=False)
    def confirm_sale(self, order_id, **kwargs):
        started = time.monotonic()
        route = '%s/sales/%d/action_confirm' % (API_ROOT, order_id)
        token, uid, client, refusal = self._require_auth(
            route, started, 'sales:write')
        if refusal is not None:
            return refusal

        module_err = self._check_model_installed('sale.order', 'sale')
        if module_err is not None:
            self._log(route, 422, started, client=client, scope='sales:write')
            return module_err

        try:
            order = request.env['sale.order'].with_user(uid).browse(order_id)
            if not order.exists():
                self._log(route, 404, started, client=client, scope='sales:write')
                return _error('not_found', "sale order %d not found" % order_id, 404)
            order.action_confirm()
            res = order.read(SALE_READ_FIELDS)[0]
        except AccessError:
            self._log(route, 403, started, client=client, scope='sales:write')
            return _error('access_denied',
                          "the user this client acts as cannot confirm sales orders",
                          403)
        except (ValidationError, UserError) as exc:
            self._log(route, 400, started, client=client, scope='sales:write')
            return _error('validation_error', str(exc), 400)

        self._log(route, 200, started, client=client, scope='sales:write')
        return _json(res)
