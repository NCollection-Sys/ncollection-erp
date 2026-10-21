# -*- coding: utf-8 -*-
"""View: the printed document, inline, in a new browser tab, named after the document.

``/ncollection/document/view/<model>/<id>`` renders the document with ``record._nc_print_report()`` and
``record._nc_print_context()`` -- exactly the report and context Print uses (``nc.document.print.mixin``) -- via the
report engine, as the user, and returns the PDF with ``Content-Disposition: inline; filename=<the document's own print
name>``. So the browser shows it in its native PDF viewer (no download is forced) and the tab and the file name read
"INV-2026-00012", not the record id. Print is the same report through Odoo's download route; the content is identical.

Nothing is stored and nothing is rendered by any other template: this is the one place that serves View. Only a model
that opted in to the mixin can be named, and the user must be allowed to read the record.
"""
import re
import time

from werkzeug.exceptions import Forbidden, NotFound

from odoo import http
from odoo.exceptions import AccessError
from odoo.http import content_disposition, request
from odoo.tools.safe_eval import safe_eval

_UNSAFE = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def pdf_filename(report, record):
    """The report's own print name for ``record`` (``print_report_name``), made safe for a file name."""
    name = ''
    if report.print_report_name:
        try:
            name = safe_eval(report.print_report_name, {'object': record, 'time': time})
        except Exception:                                           # noqa: BLE001 - fall back to the record name
            name = ''
    name = _UNSAFE.sub('-', (name or record.display_name or report.name or 'document')).strip(' .-') or 'document'
    return name[:120] + '.pdf'


class DocumentView(http.Controller):

    @http.route('/ncollection/document/view/<string:model>/<int:record_id>', type='http', auth='user',
                methods=['GET'], readonly=False, sitemap=False)
    def view(self, model, record_id, **_ignored):
        Model = request.env.get(model)
        if Model is None or not getattr(Model, '_nc_printable', False):
            raise NotFound()                                           # only documents that opted in can be viewed
        record = Model.browse(record_id).exists()
        if not record:
            raise NotFound()
        try:
            record.check_access('read')
        except AccessError:
            raise Forbidden()
        # EXACTLY Print's call: the same report, the same context, no extra flag (a flag only View passed would make
        # a report that re-uses a stored PDF read differently here than it does when printed).
        report = record._nc_print_report()
        pdf = report.with_context(**record._nc_print_context())._render_qweb_pdf(report.report_name, record.ids)[0]
        return request.make_response(pdf, headers=[
            ('Content-Type', 'application/pdf'),
            ('Content-Length', len(pdf)),
            ('Content-Disposition', content_disposition(pdf_filename(report, record), 'inline')),
            ('Cache-Control', 'no-store'),
            ('X-Content-Type-Options', 'nosniff'),
        ])
