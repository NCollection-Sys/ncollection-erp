# -*- coding: utf-8 -*-
"""``GET /ncollection/premium/<sales|invoicing>/export/<xlsx|pdf>``.

A signed-in session; the company is the session's active one (no parameter names a tenant or a company); the dashboard's
own role gate decides access (403). The figures are computed on the server from the records, never taken from the client.
"""
from werkzeug.exceptions import BadRequest, Forbidden

from odoo import http
from odoo.exceptions import AccessError, UserError
from odoo.http import content_disposition, request

_TYPES = {
    'pdf': 'application/pdf',
    'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
}


class PremiumDashboardExport(http.Controller):

    @http.route('/ncollection/premium/<string:kind>/export/<string:fmt>', type='http', auth='user', methods=['GET'],
                readonly=True)
    def export(self, kind, fmt, date_from=None, date_to=None, **_kwargs):
        if kind not in ('sales', 'invoicing') or fmt not in _TYPES:
            raise BadRequest()
        env = request.env
        try:
            payload = env['ncollection.premium.dashboard'].payload(kind, date_from or None, date_to or None)
        except AccessError:
            raise Forbidden()
        except UserError as error:
            raise BadRequest(str(error))
        if not payload.get('available'):
            raise Forbidden()
        service = env['ncollection.premium.dashboard.export']
        body = service.build_xlsx(kind, payload) if fmt == 'xlsx' else service.build_pdf(kind, payload)
        return request.make_response(body, headers=[
            ('Content-Type', _TYPES[fmt]), ('Content-Length', len(body)),
            ('Content-Disposition', content_disposition(service.filename(kind, payload, fmt))),
            ('X-Content-Type-Options', 'nosniff'), ('Cache-Control', 'no-store')])
