# -*- coding: utf-8 -*-
"""#250: stream report XLSX instead of persisting an ir.attachment.

``action_export_xlsx`` used to build the workbook, store it as an
``ir.attachment`` pointing at the **transient** report wizard, and hand back a
``/web/content/<id>`` URL. When Odoo's autovacuum reclaimed the wizard the
attachment was left with a dangling ``res_id`` and nothing ever collected it — a
slow storage leak across what is now NINE report models.

Streaming removes the leak by removing the artefact. It also removes the
download-by-id surface: there is no longer a stored object whose id could be
guessed, which is why the #111 reviewers preferred this over a pruning cron.

Two behaviour changes, both deliberate:

* the download must happen while the wizard still exists, rather than forever.
  Note the two thresholds are NOT the same: ``_transient_max_hours`` (1.0h)
  is when a row becomes *eligible*, but the deletion is performed by
  ``ir.autovacuum``, whose cron runs **daily** — so in practice a wizard
  survives well past an hour. Clicking Export is immediate either way, so this
  is the normal path;
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
        # `_abstract` is load-bearing, not belt-and-braces: `isinstance` is
        # REFLEXIVELY true for the engine itself, so without it a request for
        # `ncollection.account.report` passes the guard, and `exists()` then
        # queries a table that was never created (_auto=False) — an unhandled
        # UndefinedTable, i.e. a 500 from a one-line URL. Verified in the
        # deployed image; the forged-model test covers it.
        if model is None or not isinstance(model, type(engine)) or model._abstract:
            # Not a concrete financial report — indistinguishable from a bad
            # URL, deliberately: a distinct error would confirm which models
            # exist.
            raise request.not_found()

        wizard = model.browse(report_id)
        # exists() applies neither ACL nor ir.rule — it answers ONLY "was this
        # row vacuumed", which is why the access check below is separate and
        # explicit. Accepted residual: the 404-vs-403 split reveals whether a
        # report id currently exists. Low sensitivity (existence only, no owner
        # or content) and it mirrors the shipped `action_drill_down` pattern.
        if not wizard.exists():
            raise request.not_found()

        # EXPLICIT access check. The previous revision relied on a field read
        # inside _nc_build_xlsx() happening to trigger ir.rule — which is true
        # today only because every report's _nc_compute_lines() touches self.
        # _nc_report_title() does NOT: all nine overrides return a static
        # translated string. Depending on that would have been an authorization
        # guarantee no test asserts and the next report type could silently
        # break. AccessError renders as 403.
        wizard.check_access('read')

        filename = '%s.xlsx' % wizard._nc_report_title()

        # _nc_build_xlsx returns base64 — its contract predates this route and
        # two shipped tests assert it, so it is decoded here rather than changed.
        payload = base64.b64decode(wizard._nc_build_xlsx())
        return request.make_response(payload, headers=[
            ('Content-Type', _XLSX_MIMETYPE),
            ('Content-Length', len(payload)),
            ('Content-Disposition', content_disposition(filename)),
            # Financial figures — never let a shared cache or proxy retain them.
            ('Cache-Control', 'private, no-store'),
        ])
