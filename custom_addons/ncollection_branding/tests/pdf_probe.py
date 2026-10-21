# -*- coding: utf-8 -*-
"""Read stamp placements back OUT of a finished PDF, so a test checks the paper, not the code that drew on it.

A stamp is drawn by reportlab as ``q <w> 0 0 <h> <x> <y> cm /Name Do Q`` (a transform, then the image). This reads the
transform that immediately precedes each image draw (``Do``), ignoring the identity matrix a page opens with,
from each page's content stream and converts them to millimetres from the TOP-LEFT of the page, the same
frame the stamp templates use. Test support only; not imported by the module.
"""
import io

from odoo.tools.pdf import PdfFileReader, generic

MM = 72.0 / 25.4


def _operations(page, reader):
    """The page's drawing operators as (operands, operator) pairs."""
    contents = page.get_contents()
    return list(generic.ContentStream(contents, reader).operations) if contents is not None else []


def page_count(pdf_bytes):
    return len(PdfFileReader(io.BytesIO(pdf_bytes), strict=False).pages)


def placements(pdf_bytes):
    """``[[(x_mm, y_mm, width_mm, height_mm) of each stamp on page 1], [... page 2], ...]``."""
    reader = PdfFileReader(io.BytesIO(pdf_bytes), strict=False)
    result = []
    for page in reader.pages:
        height_pt = float(page.mediabox.height)
        found = []
        matrix = None
        for operands, operator in _operations(page, reader):
            if operator == b'cm' and len(operands) == 6:
                matrix = [float(value) for value in operands]       # remembered until what it positions is drawn
            elif operator == b'Do' and matrix:                       # an image drawn: that matrix IS its placement
                width, _b, _c, height, x, y = matrix
                found.append((x / MM, (height_pt - y - height) / MM, width / MM, height / MM))
        result.append(found)
    return result


def has_soft_mask(pdf_bytes):
    """True when an image on the page carries an alpha channel (a transparent PNG kept transparent)."""
    reader = PdfFileReader(io.BytesIO(pdf_bytes), strict=False)

    def walk(resources, depth=0):
        if depth > 4 or not resources:
            return False
        for xobject in (resources.get('/XObject') or {}).values():
            xobject = xobject.get_object()
            if '/SMask' in xobject or walk(xobject.get('/Resources'), depth + 1):
                return True
        return False

    return any(walk(page.get('/Resources')) for page in reader.pages)
