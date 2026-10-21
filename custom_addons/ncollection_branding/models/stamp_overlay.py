# -*- coding: utf-8 -*-
"""Put stamp images onto a finished PDF, in MILLIMETRES, on the page's real size.

Why an overlay and not a template block. A stamp is placed on the paper, not in the document flow: "40 mm from the
left, 220 mm from the top, 38 x 38 mm, on the last page". wkhtmltopdf lays a document out as one long web page that it
slices into sheets, so a coordinate inside the template does not mean a coordinate on the paper (headers, margins and
page breaks all move it), and "last page" cannot be expressed at all. Drawing onto the finished page can: its own
media box is the truth, so A4 portrait, A4 landscape, Letter or any custom format work with the same numbers.

This module knows nothing about companies, documents or stamps as records: it is given PNG bytes and a placement and
returns PDF bytes. Selection (WHICH stamp goes on WHICH document) is ``ncollection.document.stamp``.

Coordinates: origin at the TOP-LEFT of the page, x to the right, y downward, like a ruler on the sheet. (PDF's own
origin is bottom-left; the conversion is done here, once.)
"""
import io

from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from odoo.tools.pdf import PdfFileReader, PdfFileWriter

POINTS_PER_MM = 72.0 / 25.4          # a PDF point is 1/72 inch

PAGE_SCOPES = ('first', 'last', 'all')


def page_size_mm(pdf_bytes, page_index=0):
    """``(width, height)`` of one page in millimetres, from the PDF itself."""
    reader = PdfFileReader(io.BytesIO(pdf_bytes), strict=False)
    box = reader.pages[page_index % len(reader.pages)].mediabox         # -1 = the last page
    return float(box.width) / POINTS_PER_MM, float(box.height) / POINTS_PER_MM


def blank_pdf(width_mm, height_mm, label=None):
    """A one-page PDF of the given size with a light millimetre ruler: the sheet a stamp is previewed on when there is
    no real document of that kind yet. The ruler is drawn every 10 mm and labelled every 50 mm."""
    buffer = io.BytesIO()
    sheet = canvas.Canvas(buffer, pagesize=(width_mm * POINTS_PER_MM, height_mm * POINTS_PER_MM))
    sheet.setStrokeColorRGB(0.86, 0.89, 0.93)
    sheet.setLineWidth(0.3)
    sheet.setFillColorRGB(0.45, 0.52, 0.6)
    sheet.setFont('Helvetica', 6)
    for mm in range(0, int(width_mm) + 1, 10):
        sheet.line(mm * POINTS_PER_MM, 0, mm * POINTS_PER_MM, height_mm * POINTS_PER_MM)
        if mm % 50 == 0:
            sheet.drawString(mm * POINTS_PER_MM + 1.5, height_mm * POINTS_PER_MM - 8, '%d mm' % mm)
    for mm in range(0, int(height_mm) + 1, 10):
        y = (height_mm - mm) * POINTS_PER_MM
        sheet.line(0, y, width_mm * POINTS_PER_MM, y)
        if mm % 50 == 0 and mm:
            sheet.drawString(1.5, y + 1.5, '%d mm' % mm)
    if label:
        sheet.setFont('Helvetica', 8)
        sheet.drawString(10 * POINTS_PER_MM, (height_mm - 6) * POINTS_PER_MM - 4, label)
    sheet.showPage()
    sheet.save()
    return buffer.getvalue()


def _pages_for(scope, index, count):
    if scope == 'all':
        return True
    if scope == 'last':
        return index == count - 1
    return index == 0                       # 'first', and anything unexpected: the safe, single page


def overlay_stamps(pdf_bytes, placements):
    """Return ``pdf_bytes`` with every placement drawn on its page(s).

    ``placements``: list of dicts ``image`` (PNG bytes), ``x_mm``, ``y_mm`` (the stamp's TOP-LEFT corner from the
    page's top-left), ``width_mm``, ``height_mm`` and ``pages`` (``first`` / ``last`` / ``all``). A PNG's transparency
    is kept. With no placement the very same bytes come back, untouched, so a document without a stamp is never
    re-written.
    """
    if not placements:
        return pdf_bytes
    reader = PdfFileReader(io.BytesIO(pdf_bytes), strict=False)
    count = len(reader.pages)
    images = [ImageReader(io.BytesIO(item['image'])) for item in placements]
    writer = PdfFileWriter()
    for index, page in enumerate(reader.pages):
        wanted = [(item, image) for item, image in zip(placements, images)
                  if _pages_for(item.get('pages', 'first'), index, count)]
        if wanted:
            width, height = float(page.mediabox.width), float(page.mediabox.height)
            buffer = io.BytesIO()
            sheet = canvas.Canvas(buffer, pagesize=(width, height))
            for item, image in wanted:
                box_w = item['width_mm'] * POINTS_PER_MM
                box_h = item['height_mm'] * POINTS_PER_MM
                x = item['x_mm'] * POINTS_PER_MM
                y = height - item['y_mm'] * POINTS_PER_MM - box_h            # top-left in mm -> bottom-left in points
                sheet.drawImage(image, x, y, width=box_w, height=box_h, mask='auto', preserveAspectRatio=False)
            sheet.showPage()
            sheet.save()
            buffer.seek(0)
            page.merge_page(PdfFileReader(buffer, strict=False).pages[0])
        writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
