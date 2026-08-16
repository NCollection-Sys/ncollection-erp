# -*- coding: utf-8 -*-
"""P8-T02: Stock levels business endpoints (/api/v1/stock/levels)."""
import time

from odoo import http
from odoo.exceptions import AccessError
from odoo.http import request

from .common import API_ROOT, ApiControllerBase, _error, _json

STOCK_QUANT_FIELDS = [
    'id', 'product_id', 'location_id', 'quantity', 'available_quantity'
]


class StockApiController(ApiControllerBase):

    @http.route('%s/stock/levels' % API_ROOT, type='http', auth='none',
                methods=['GET'], csrf=False, save_session=False,
                readonly=False)
    def list_stock_levels(self, limit=None, offset=None, product_id=None,
                          location_id=None, **kwargs):
        started = time.monotonic()
        route = '%s/stock/levels' % API_ROOT
        token, uid, client, refusal = self._require_auth(
            route, started, 'stock:read')
        if refusal is not None:
            return refusal

        module_err = self._check_model_installed('stock.quant', 'stock')
        if module_err is not None:
            self._log(route, 422, started, client=client, scope='stock:read')
            return module_err

        limit, offset, refusal = self._parse_pagination(limit, offset)
        if refusal is not None:
            self._log(route, 400, started, client=client, scope='stock:read')
            return refusal

        domain = [('location_id.usage', '=', 'internal')]
        if product_id:
            try:
                domain.append(('product_id', '=', int(product_id)))
            except ValueError:
                self._log(route, 400, started, client=client, scope='stock:read')
                return _error('invalid_request', "product_id must be an integer", 400)
        if location_id:
            try:
                domain.append(('location_id', '=', int(location_id)))
            except ValueError:
                self._log(route, 400, started, client=client, scope='stock:read')
                return _error('invalid_request', "location_id must be an integer", 400)

        try:
            Quant = request.env['stock.quant'].with_user(uid)
            quants = Quant.search_read(
                domain, STOCK_QUANT_FIELDS, limit=limit, offset=offset)
        except AccessError:
            self._log(route, 403, started, client=client, scope='stock:read')
            return _error('access_denied',
                          "the user this client acts as cannot read stock levels",
                          403)

        self._log(route, 200, started, client=client, scope='stock:read')
        return _json({'count': len(quants), 'results': quants})
