# -*- coding: utf-8 -*-
import hashlib
import logging
from datetime import timedelta

from odoo import fields, http
from odoo.exceptions import AccessError, UserError
from odoo.http import Response, request

from ..models import renderer
from ..models.print_job import DOWNLOAD_WINDOW_MINUTES

_logger = logging.getLogger(__name__)
_MODES = ('design', 'print', 'calibration')


def _pdf(content, filename, download=False):
    """The PDF as an ordinary, well-behaved file response.

    A browser's viewer and a download manager (IDM) both treat this as a file, so it behaves like one:
    ``Content-Type: application/pdf``, an exact ``Content-Length``, ``Accept-Ranges: bytes`` with real 206 answers
    for the ``Range`` probes download managers send, a correct answer to HEAD, and an RFC 6266 filename. ``inline``
    opens it in the viewer; ``download=1`` sends it as an attachment so it can always be saved. It is never cached
    (the payee and amount are in it) and never wrapped in a page: the body IS the PDF."""
    from urllib.parse import quote
    disposition = 'attachment' if download else 'inline'
    response = Response(content, status=200, mimetype='application/pdf', direct_passthrough=False)
    response.headers['Content-Disposition'] = "%s; filename=\"%s\"; filename*=UTF-8''%s" % (
        disposition, filename.replace('"', ''), quote(filename))
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Accept-Ranges'] = 'bytes'
    response.headers['Content-Length'] = str(len(content))
    # Range / If-Range / HEAD handling is werkzeug's own: 206 + Content-Range for a probe, 200 for a plain GET
    return response.make_conditional(request.httprequest, accept_ranges=True, complete_length=len(content))


def _text(status, message):
    # `message` is already translated (request.env._) or a server-built string
    return request.make_response(message, headers=[('Content-Type', 'text/plain; charset=utf-8')], status=status)


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


class ChequeController(http.Controller):

    @http.route('/ncollection/cheque/preview', type='http', auth='user', methods=['GET'], sitemap=False)
    def preview(self, template=None, mode='print', payment=None, profile=None, cheque_number='', lang=None,
                download=None, **_ignored):
        """An on-screen check. It reads with the CALLER's rights, creates nothing and writes nothing, and the print
        preview carries a watermark so it is never mistaken for a print (a print has its own audited path)."""
        env = request.env
        if mode not in _MODES:
            return _text(400, env._("Unknown preview mode."))
        tpl = env['ncollection.cheque.template'].browse(_int(template)).exists()
        try:
            if not tpl:
                return _text(404, env._("No such cheque template."))
            tpl.check_access('read')
            profile_rec = env['ncollection.cheque.printer.profile'].browse(_int(profile)).exists()
            if profile_rec:
                profile_rec.check_access('read')
            profile_rec = profile_rec or tpl.printer_profile_id
            if _int(payment):
                pay = env['account.payment'].browse(_int(payment)).exists()
                pay.check_access('read')
                env['ncollection.cheque.print.job']._check_can_print(pay, tpl)
                raw = env['ncollection.cheque.values'].from_payment(pay, cheque_number[:40], lang)
            else:
                raw = env['ncollection.cheque.values'].sample(lang)
        except (AccessError, UserError) as exc:
            return _text(403 if isinstance(exc, AccessError) else 400, str(exc))
        pdf, _warnings = renderer.render_pdf(
            tpl._spec(), raw, mode=mode, profile=profile_rec._as_dict() if profile_rec else {},
            background=tpl._background_bytes(), preview=(mode == 'print'),
            labels=env['ncollection.cheque.values'].labels(raw['lang']))
        return _pdf(pdf, 'cheque-%s-%s.pdf' % (mode, tpl.id), download=download == '1')

    @http.route('/ncollection/cheque/download/<int:event_id>', type='http', auth='user', methods=['GET'],
                sitemap=False)
    def download(self, event_id, download=None, **_ignored):
        """The PDF of ONE audited print or reprint, for the person who did it, for a short time.

        A print is recorded when it is made, with a fingerprint of the PDF. This link is the delivery of THAT print:
        it expires, it belongs to the user who printed, and it renders the same bytes again from the snapshot. A new
        copy later is a reprint, with its own reason, not a replay of this URL."""
        env = request.env
        event = env['ncollection.cheque.print.event'].browse(event_id).exists()
        try:
            if event:
                event.check_access('read')
        except AccessError:
            event = env['ncollection.cheque.print.event']
        if not event or event.kind not in ('print', 'reprint') or event.user_id != env.user:
            return _text(403, env._("This cheque download is not available."))
        if event.at < fields.Datetime.now() - timedelta(minutes=DOWNLOAD_WINDOW_MINUTES):
            return _text(403, env._(
                "This download has expired. Reprint the cheque (with a reason) to get a new copy."))
        job = event.job_id
        pdf, _warnings = job._render('print')
        if hashlib.sha256(pdf).hexdigest() != event.pdf_sha256:
            _logger.warning("cheque print: event %s no longer renders to the recorded fingerprint", event.id)
            return _text(409, env._("The template or the payment changed after this print. Reprint the cheque."))
        return _pdf(pdf, 'cheque-%s.pdf' % (job.name or job.id), download=download == '1')
