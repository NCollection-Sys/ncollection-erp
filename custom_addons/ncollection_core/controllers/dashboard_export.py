# -*- coding: utf-8 -*-
"""``/ncollection/dashboard/export/<pdf|xlsx>`` (DASH-02).

A signed-in session, always. The database is the session's own: no parameter names a tenant, a user or a model. The
optional ``company_id`` must be one of the user's allowed companies. Which figures the file holds is decided by the
dashboard's own payload (role, licence, access rules): a role that cannot see a card does not get it in the file.
"""
from werkzeug.exceptions import BadRequest, Forbidden

from odoo import http
from odoo.exceptions import AccessError, UserError
from odoo.http import content_disposition, request

_TYPES = {
    'pdf': 'application/pdf',
    'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
}


class DashboardExport(http.Controller):

    @http.route('/ncollection/dashboard/export/<string:fmt>', type='http', auth='user', methods=['GET'],
                readonly=True)
    def export(self, fmt, date_from=None, date_to=None, company_id=None, **_kwargs):
        if fmt not in _TYPES:
            raise BadRequest()
        env = request.env
        if company_id:
            try:
                company_id = int(company_id)
            except ValueError:
                raise BadRequest()
            if company_id not in env.user.company_ids.ids:
                raise Forbidden()
            env = env(context=dict(env.context, allowed_company_ids=[company_id]))
        service = env['ncollection.dashboard.export']
        try:
            payload = service._export_payload(date_from or None, date_to or None)
            body = service.build_xlsx(payload) if fmt == 'xlsx' else service.build_pdf(payload)
        except UserError as error:                      # a bad period, an unlicensed app: the dashboard's own words
            if isinstance(error, AccessError):
                raise Forbidden()
            raise BadRequest(str(error))
        if not payload['widgets']:
            raise Forbidden()                           # nothing this user may see: nothing to export
        filename = service.export_filename(payload, fmt)
        return request.make_response(body, headers=[
            ('Content-Type', _TYPES[fmt]), ('Content-Length', len(body)),
            ('Content-Disposition', content_disposition(filename)), ('X-Content-Type-Options', 'nosniff'),
            ('Cache-Control', 'no-store')])
