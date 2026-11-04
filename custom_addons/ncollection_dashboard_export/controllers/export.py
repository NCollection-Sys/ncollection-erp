# -*- coding: utf-8 -*-
"""``POST /ncollection/dashboards/export/<xlsx|pdf>`` - the Export button of the Dashboards page.

A signed-in session, a CSRF token, and a dashboard the user can READ (Odoo's own access rules decide: a dashboard
the user cannot open cannot be exported by naming its id). The database is the session's own: no parameter names a
tenant, a company or a user. The body is a snapshot of what the page shows, validated and bounded by the service.
"""
import json

from werkzeug.exceptions import BadRequest, Forbidden, NotFound, RequestEntityTooLarge

from odoo import http
from odoo.exceptions import AccessError, ValidationError
from odoo.http import content_disposition, request

_TYPES = {
    'pdf': 'application/pdf',
    'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
}
_MAX_BODY = 6 * 1024 * 1024


class SpreadsheetDashboardExport(http.Controller):

    @http.route('/ncollection/dashboards/export/<string:fmt>', type='http', auth='user', methods=['POST'],
                csrf=True, readonly=True)
    def export(self, fmt, dashboard_id=None, payload=None, **_kwargs):
        if fmt not in _TYPES:
            raise BadRequest()
        try:
            dashboard = request.env['spreadsheet.dashboard'].browse(int(dashboard_id))
        except (TypeError, ValueError):
            raise BadRequest()
        if not dashboard.exists():
            raise NotFound()
        try:
            dashboard.check_access('read')
        except AccessError:
            raise Forbidden()
        if not payload or len(payload) > _MAX_BODY:
            raise RequestEntityTooLarge() if payload else BadRequest()
        try:
            raw = json.loads(payload)
        except ValueError:
            raise BadRequest()
        service = request.env['ncollection.spreadsheet.dashboard.export']
        try:
            snapshot = service.clean_snapshot(raw)
        except (ValidationError, AttributeError, TypeError):
            raise BadRequest()
        snapshot['name'] = dashboard.name or snapshot['name']          # the stored name, not the client's word
        body = service.build_xlsx(snapshot) if fmt == 'xlsx' else service.build_pdf(snapshot)
        filename = service.export_filename(snapshot['name'], fmt)
        return request.make_response(body, headers=[
            ('Content-Type', _TYPES[fmt]), ('Content-Length', len(body)),
            ('Content-Disposition', content_disposition(filename)), ('X-Content-Type-Options', 'nosniff'),
            ('Cache-Control', 'no-store')])
