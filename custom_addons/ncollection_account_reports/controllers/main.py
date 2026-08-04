# -*- coding: utf-8 -*-
"""#250: stream report XLSX instead of persisting an ir.attachment.

``action_export_xlsx`` used to build the workbook, store it as an
``ir.attachment`` pointing at the **transient** report wizard, and hand back a
``/web/content/<id>`` URL. When Odoo's autovacuum reclaimed the wizard (~1h) the
attachment was left with a dangling ``res_id`` and nothing ever collected it — a
slow storage leak across what is now NINE report models.

Streaming removes the leak by removing the artefact. It also removes the
download-by-id surface: there is no longer a stored object whose id could be
guessed, which is why the #111 reviewers preferred this over a pruning cron.

Two behaviour changes, both deliberate:

* the download must happen while the wizard lives (~1h) rather than forever —
  clicking Export is an immediate action, so this is the normal path;
* the workbook is rebuilt at download time rather than at click time. Same
  filters, so the same numbers unless the underlying data moved in between.
"""
import base64

from odoo import http
from odoo.http import content_disposition, request

_XLSX_MIMETYPE = ('application/vnd.openxmlformats-officedocument.'
                  'spreadsheetml.sheet')


class NcollectionAccountReportXlsx(http.Controller):

    @http.route('/ncollection/account_reports/xlsx/<string:report_model>/<int:report_id>',
                type='http', auth='user', methods=['GET'])
    def download_xlsx(self, report_model, report_id, **kwargs):
        """Render a report run to XLSX and stream it. Never persists anything.

        SECURITY — this route takes a model name from the URL, which is the
        same forgery surface ``action_drill_down`` had before #313. The same
        control applies here and for the same reason: resolving through the
        report engine's own class fails closed, so only models built on the
        engine are reachable, and it needs no hand-maintained allow-list.

        Ownership is NOT re-implemented here. ``browse`` runs as the requesting
        user, so the global ``create_uid`` ``ir.rule`` on every report wizard
        (#111/#113) denies someone else's run by itself — re-checking it in the
        controller would be a second, drifting copy of the same rule.
        """
        engine = request.env['ncollection.account.report']
        model = request.env.get(report_model)
        if model is None or not isinstance(model, type(engine)):
            # Not a financial report model — indistinguishable from a bad URL,
            # deliberately: a distinct error would confirm which models exist.
            raise request.not_found()

        wizard = model.browse(report_id)
        # exists() is a raw SELECT that applies neither ACL nor ir.rule, so it
        # can only be trusted to answer "was this row vacuumed"; the access
        # decision comes from the field read below, which does enforce them.
        if not wizard.exists():
            raise request.not_found()

        # Reading a field forces ACL + ir.rule; another user's run raises
        # AccessError here, which Odoo renders as a 403.
        filename = '%s.xlsx' % wizard._nc_report_title()

        # _nc_build_xlsx returns base64 — its contract predates this route and
        # two shipped tests assert it, so it is decoded here rather than changed.
        payload = base64.b64decode(wizard._nc_build_xlsx())
        return request.make_response(payload, headers=[
            ('Content-Type', _XLSX_MIMETYPE),
            ('Content-Length', len(payload)),
            ('Content-Disposition', content_disposition(filename)),
        ])
