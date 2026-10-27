# -*- coding: utf-8 -*-
"""Stamp each record's PDF as it is produced, so every way of obtaining a document returns the same stamped document.

Odoo builds ONE PDF per printed record (``_render_qweb_pdf_prepare_streams``) and only afterwards merges them and saves
the attachment. Stamping there, per record, means:
  * the company and the document type are those of THAT record (a batch of invoices of two companies is stamped
    company by company),
  * View, Print, an emailed PDF and a stored attachment all come out of the same place, so they cannot differ,
  * a PDF Odoo re-uses from a stored attachment is NOT stamped a second time (it was stamped when it was stored).
Nothing else about rendering changes: this wraps Odoo's method, it does not replace it.
"""
import io

from odoo import models


class IrActionsReport(models.Model):
    _inherit = 'ir.actions.report'

    def get_paperformat(self):
        """The paper a document prints on, in this order: the report's own (a cheque declares its exact size), the
        Print Settings of its document type, the NCollection document paper when the company prints with the
        NCollection letterhead (Odoo's stock A4 reserves the 40 mm of a boxed header, a blank band above this
        compact one), then Odoo's company paper. Preview and Print both come through here."""
        if self.paperformat_id:
            return self.paperformat_id
        chosen = self.env['ncollection.document.type']._nc_paperformat_for(self) if self else False
        if chosen:
            return chosen
        layout = self.env.ref('ncollection_branding.external_layout_nc', raise_if_not_found=False)
        if layout and self.env.company.external_report_layout_id == layout:
            paper = self.env.ref('ncollection_branding.paperformat_nc_document', raise_if_not_found=False)
            if paper:
                return paper
        return super().get_paperformat()

    def _render_qweb_pdf_prepare_streams(self, report_ref, data, res_ids=None):
        collected = super()._render_qweb_pdf_prepare_streams(report_ref, data, res_ids=res_ids)
        # nc_no_stamp is for code that already runs elevated (tests, baselines): a client-sent context key must not
        # let a user print an unstamped copy of a document that carries an official stamp
        if not res_ids or (self.env.su and self.env.context.get('nc_no_stamp')):
            return collected
        report = self._get_report(report_ref)
        Stamp = self.env['ncollection.document.stamp']
        records = self.env[report.model].browse([res_id for res_id in collected if res_id])
        by_id = {record.id: record for record in records}
        for res_id, entry in collected.items():
            record = by_id.get(res_id)
            stream = entry.get('stream')
            if record is None or stream is None:
                continue                                 # a merged result for duplicated ids: nothing to attribute
            if entry.get('attachment') and report.attachment_use:
                continue                                 # Odoo re-used a stored PDF: it already carries its stamp
            kind = self.env['ncollection.document.type']._nc_kind_of(record)
            if kind and not kind.nc_show_stamp:
                continue                                 # its Print Template prints this kind without a stamp
            pdf = stream.getvalue()
            stamped = Stamp._nc_apply_to_pdf(report, record, pdf)
            if stamped is not pdf:
                entry['stream'] = io.BytesIO(stamped)
        return collected
