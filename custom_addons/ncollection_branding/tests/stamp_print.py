# -*- coding: utf-8 -*-
"""Test helper (not a test module): how many stamps the print pipeline draws on ``record``'s PDF.

Odoo's renderer is replaced by a known one-page sheet, so what is counted is what the stamp overlay
(``ir_actions_report._render_qweb_pdf_prepare_streams``) adds to the PDF that leaves the pipeline.
Shared by the modules
whose documents carry the Document Stamp switch (accounting, sales, purchase)."""
import io
from unittest.mock import patch

from reportlab.pdfgen import canvas

from odoo.addons.base.models.ir_actions_report import IrActionsReport

from . import pdf_probe

MM = 72.0 / 25.4


def _sheet():
    buffer = io.BytesIO()
    page = canvas.Canvas(buffer, pagesize=(210 * MM, 297 * MM))
    page.drawString(20 * MM, 20 * MM, 'document')
    page.showPage()
    page.save()
    return buffer.getvalue()


def stamps_on(report, record):
    """The number of stamps drawn on each page of ``record`` printed by ``report``."""
    def rendered(_self, report_ref, data, res_ids=None):
        return {res_id: {'stream': io.BytesIO(_sheet()), 'attachment': None} for res_id in res_ids}

    with patch.object(IrActionsReport, '_render_qweb_pdf_prepare_streams', rendered):
        streams = report._render_qweb_pdf_prepare_streams(report.report_name, {}, res_ids=record.ids)
    return [len(found) for found in pdf_probe.placements(streams[record.id]['stream'].getvalue())]
