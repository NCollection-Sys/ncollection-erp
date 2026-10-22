# -*- coding: utf-8 -*-
"""The stamp designer's preview: a REAL PDF, produced by the same engine that stamps a printed document.

``/ncollection/stamp/preview/<stamp id>?x=..&y=..&w=..&h=..&pages=..`` renders the most recent real document of the
stamp's first document type (a posted invoice, a quotation...) through its own report and stamps it with this template
at the geometry in the query string (the values being dragged, not yet saved). When the workspace has no such document
yet, the stamp is drawn by the very same code on a blank sheet of the right paper size with a millimetre ruler.

Nothing is invented here: the document comes from the report engine as the user, the stamp from
``ncollection.document.stamp._nc_apply_to_pdf``. The response is shown inline (the browser's PDF viewer), never saved.
"""
import ast
import logging

from werkzeug.exceptions import NotFound

from odoo import http
from odoo.exceptions import AccessError
from odoo.http import request

from ..models import stamp_overlay
from ..models.document_stamp import PAGE_SCOPES, sheet_mm

_logger = logging.getLogger(__name__)


def _number(value, low, high):
    """A query-string number inside [low, high], or None (use the saved value)."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return min(max(number, low), high)


class StampPreview(http.Controller):

    @http.route('/ncollection/stamp/preview/<int:stamp_id>', type='http', auth='user', methods=['GET'],
                readonly=False, sitemap=False)
    def preview(self, stamp_id, x=None, y=None, w=None, h=None, pages=None, **_ignored):
        stamp = request.env['ncollection.document.stamp'].browse(stamp_id).exists()
        if not stamp:
            raise NotFound()
        try:
            # the preview is a DESIGNER tool and draws the (group-restricted) seal: whoever may not edit the stamp
            # may not see it
            stamp.check_access('write')
            stamp._nc_check_kinds_allowed()
        except AccessError:
            raise NotFound()                               # not even the existence of another company's stamp
        override = {
            'stamp_id': stamp.id,
            'x_mm': _number(x, 0, 2000), 'y_mm': _number(y, 0, 2000),
            'width_mm': _number(w, 1, 400), 'height_mm': _number(h, 1, 400),
            'pages': pages if pages in dict(PAGE_SCOPES) else None,
        }
        pdf = self._real_document(stamp, override)
        if pdf is None:
            width, height = sheet_mm(stamp._nc_paperformat())
            blank = stamp_overlay.blank_pdf(
                width, height, request.env._("Sample sheet: there is no %(kind)s yet, so the stamp is shown on an "
                                             "empty page.", kind=stamp.document_type_ids[:1].name or ''))
            pdf = stamp_overlay.overlay_stamps(blank, [stamp._nc_placement(override)])
        response = request.make_response(pdf, headers=[
            ('Content-Type', 'application/pdf'), ('Content-Length', len(pdf)),
            ('Content-Disposition', 'inline; filename="stamp-preview.pdf"'),
            ('Cache-Control', 'no-store'), ('X-Content-Type-Options', 'nosniff')])
        return response

    @staticmethod
    def _real_document(stamp, override):
        """The newest real document of this stamp's first document type, printed and stamped, or None."""
        kind = stamp.document_type_ids[:1]
        report = kind.report_ids[:1]
        if not report:
            return None
        env = request.env
        try:
            Model = env[report.model]
            domain = ast.literal_eval(kind.record_domain or '[]')
            if 'company_id' in Model._fields:
                domain = domain + [('company_id', '=', stamp.company_id.id)]
            record = Model.search(domain, order='id desc', limit=1)
            if not record:
                return None
            context = {'nc_stamp_preview': override, 'report_pdf_no_attachment': True}
            if hasattr(record, '_nc_print_context'):
                context.update(record._nc_print_context())
            return report.with_context(**context)._render_qweb_pdf(report.report_name, record.ids)[0]
        except Exception:                                              # noqa: BLE001 - a preview must never be a crash
            _logger.warning("Stamp preview could not print a real %s; showing the sample sheet.", kind.code,
                            exc_info=True)
            return None
