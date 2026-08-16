# -*- coding: utf-8 -*-
"""P8-T02: Products business endpoints (/api/v1/products)."""
import time

from odoo import http
from odoo.exceptions import AccessError, ValidationError
from odoo.http import request

from .common import API_ROOT, ApiControllerBase, _error, _json

PRODUCT_READ_FIELDS = [
    'id', 'name', 'default_code', 'list_price', 'standard_price', 'type',
    'active'
]


class ProductsApiController(ApiControllerBase):

    @http.route('%s/products' % API_ROOT, type='http', auth='none',
                methods=['GET'], csrf=False, save_session=False,
                readonly=False)
    def list_products(self, limit=None, offset=None, name=None, default_code=None,
                      type=None, **kwargs):
        started = time.monotonic()
        route = '%s/products' % API_ROOT
        token, uid, client, refusal = self._require_auth(
            route, started, 'products:read')
        if refusal is not None:
            return refusal

        module_err = self._check_model_installed('product.template', 'product')
        if module_err is not None:
            self._log(route, 422, started, client=client, scope='products:read')
            return module_err

        limit, offset, refusal = self._parse_pagination(limit, offset)
        if refusal is not None:
            self._log(route, 400, started, client=client, scope='products:read')
            return refusal

        domain = []
        if name:
            domain.append(('name', 'ilike', name))
        if default_code:
            domain.append(('default_code', 'ilike', default_code))
        if type:
            domain.append(('type', '=', type))

        try:
            Product = request.env['product.template'].with_user(uid)
            products = Product.search_read(
                domain, PRODUCT_READ_FIELDS, limit=limit, offset=offset)
        except AccessError:
            self._log(route, 403, started, client=client, scope='products:read')
            return _error('access_denied',
                          "the user this client acts as cannot read products",
                          403)

        self._log(route, 200, started, client=client, scope='products:read')
        return _json({'count': len(products), 'results': products})

    @http.route('%s/products' % API_ROOT, type='http', auth='none',
                methods=['POST'], csrf=False, save_session=False,
                readonly=False)
    def create_product(self, **kwargs):
        started = time.monotonic()
        route = '%s/products' % API_ROOT
        token, uid, client, refusal = self._require_auth(
            route, started, 'products:write')
        if refusal is not None:
            return refusal

        module_err = self._check_model_installed('product.template', 'product')
        if module_err is not None:
            self._log(route, 422, started, client=client, scope='products:write')
            return module_err

        body, refusal = self._parse_json()
        if refusal is not None:
            self._log(route, 400, started, client=client, scope='products:write')
            return refusal

        name = body.get('name')
        if not name or not str(name).strip():
            self._log(route, 400, started, client=client, scope='products:write')
            return _error('validation_error', "field 'name' is required", 400)

        create_vals = {
            'name': str(name).strip(),
            'default_code': body.get('default_code') or False,
            'list_price': float(body.get('list_price', 0.0)),
            'type': body.get('type') or 'consu',
        }

        try:
            Product = request.env['product.template'].with_user(uid)
            product = Product.create(create_vals)
            res = product.read(PRODUCT_READ_FIELDS)[0]
        except AccessError:
            self._log(route, 403, started, client=client, scope='products:write')
            return _error('access_denied',
                          "the user this client acts as cannot create products",
                          403)
        except (ValidationError, ValueError) as exc:
            self._log(route, 400, started, client=client, scope='products:write')
            return _error('validation_error', str(exc), 400)

        self._log(route, 201, started, client=client, scope='products:write')
        return _json(res, status=201)

    @http.route('%s/products/<int:product_id>' % API_ROOT, type='http',
                auth='none', methods=['GET'], csrf=False, save_session=False,
                readonly=False)
    def get_product(self, product_id, **kwargs):
        started = time.monotonic()
        route = '%s/products/%d' % (API_ROOT, product_id)
        token, uid, client, refusal = self._require_auth(
            route, started, 'products:read')
        if refusal is not None:
            return refusal

        module_err = self._check_model_installed('product.template', 'product')
        if module_err is not None:
            self._log(route, 422, started, client=client, scope='products:read')
            return module_err

        try:
            product = request.env['product.template'].with_user(uid).browse(product_id)
            if not product.exists():
                self._log(route, 404, started, client=client, scope='products:read')
                return _error('not_found', "product %d not found" % product_id, 404)
            res = product.read(PRODUCT_READ_FIELDS)[0]
        except AccessError:
            self._log(route, 403, started, client=client, scope='products:read')
            return _error('access_denied',
                          "the user this client acts as cannot read products",
                          403)

        self._log(route, 200, started, client=client, scope='products:read')
        return _json(res)

    @http.route('%s/products/<int:product_id>' % API_ROOT, type='http',
                auth='none', methods=['PUT'], csrf=False, save_session=False,
                readonly=False)
    def update_product(self, product_id, **kwargs):
        started = time.monotonic()
        route = '%s/products/%d' % (API_ROOT, product_id)
        token, uid, client, refusal = self._require_auth(
            route, started, 'products:write')
        if refusal is not None:
            return refusal

        module_err = self._check_model_installed('product.template', 'product')
        if module_err is not None:
            self._log(route, 422, started, client=client, scope='products:write')
            return module_err

        body, refusal = self._parse_json()
        if refusal is not None:
            self._log(route, 400, started, client=client, scope='products:write')
            return refusal

        try:
            product = request.env['product.template'].with_user(uid).browse(product_id)
            if not product.exists():
                self._log(route, 404, started, client=client, scope='products:write')
                return _error('not_found', "product %d not found" % product_id, 404)

            update_vals = {}
            for field in ('name', 'default_code', 'type'):
                if field in body:
                    update_vals[field] = body[field]
            if 'list_price' in body:
                update_vals['list_price'] = float(body['list_price'])

            if update_vals:
                product.write(update_vals)
            res = product.read(PRODUCT_READ_FIELDS)[0]
        except AccessError:
            self._log(route, 403, started, client=client, scope='products:write')
            return _error('access_denied',
                          "the user this client acts as cannot update products",
                          403)
        except (ValidationError, ValueError) as exc:
            self._log(route, 400, started, client=client, scope='products:write')
            return _error('validation_error', str(exc), 400)

        self._log(route, 200, started, client=client, scope='products:write')
        return _json(res)
