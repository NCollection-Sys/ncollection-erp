# -*- coding: utf-8 -*-
"""The overlay engine on its own: millimetres on the page's real size, page scopes, transparency, no-op.

Synthetic PDFs and a synthetic PNG only. Every position is read back out of the finished PDF (tests/pdf_probe.py).
"""
import io

from PIL import Image
from reportlab.pdfgen import canvas

from odoo.tests import TransactionCase, tagged

from ..models import stamp_overlay
from . import pdf_probe

MM = stamp_overlay.POINTS_PER_MM


def _png(size=(60, 40), color=(200, 30, 30, 140)):
    buffer = io.BytesIO()
    Image.new('RGBA', size, color).save(buffer, 'PNG')
    return buffer.getvalue()


def _pages(count, width_mm=210.0, height_mm=297.0):
    buffer = io.BytesIO()
    sheet = canvas.Canvas(buffer, pagesize=(width_mm * MM, height_mm * MM))
    for number in range(count):
        sheet.drawString(20 * MM, 20 * MM, 'page %d' % (number + 1))
        sheet.showPage()
    sheet.save()
    return buffer.getvalue()


def _place(**values):
    return dict({'image': _png(), 'x_mm': 10.0, 'y_mm': 20.0, 'width_mm': 30.0, 'height_mm': 40.0,
                 'pages': 'first'}, **values)


@tagged('post_install', '-at_install')
class TestStampOverlay(TransactionCase):

    def assertPlacement(self, found, x, y, width, height):
        for got, want in zip(found, (x, y, width, height)):
            self.assertAlmostEqual(got, want, places=1)

    def test_the_stamp_lands_where_the_millimetres_say_on_a4_portrait(self):
        out = stamp_overlay.overlay_stamps(_pages(1), [_place()])
        (stamp,), = pdf_probe.placements(out)
        self.assertPlacement(stamp, 10.0, 20.0, 30.0, 40.0)

    def test_the_same_numbers_follow_the_paper_when_it_is_landscape(self):
        landscape = _pages(1, 297.0, 210.0)
        (stamp,), = pdf_probe.placements(stamp_overlay.overlay_stamps(landscape, [_place(x_mm=250.0, y_mm=150.0)]))
        self.assertPlacement(stamp, 250.0, 150.0, 30.0, 40.0)
        self.assertEqual([round(v) for v in stamp_overlay.page_size_mm(landscape)], [297, 210])

    def test_any_custom_paper_size_works_because_the_page_itself_is_the_reference(self):
        custom = _pages(1, 148.0, 105.0)                                  # A6 landscape
        (stamp,), = pdf_probe.placements(stamp_overlay.overlay_stamps(custom, [_place(x_mm=100.0, y_mm=60.0,
                                                                                      width_mm=20.0, height_mm=20.0)]))
        self.assertPlacement(stamp, 100.0, 60.0, 20.0, 20.0)

    def test_first_last_and_every_page(self):
        doc = _pages(3)
        for scope, expected in (('first', [1, 0, 0]), ('last', [0, 0, 1]), ('all', [1, 1, 1])):
            with self.subTest(scope):
                out = stamp_overlay.overlay_stamps(doc, [_place(pages=scope)])
                self.assertEqual([len(found) for found in pdf_probe.placements(out)], expected)
                self.assertEqual(pdf_probe.page_count(out), 3, "no page is lost or added")

    def test_a_document_with_no_stamp_comes_back_untouched(self):
        doc = _pages(2)
        self.assertIs(stamp_overlay.overlay_stamps(doc, []), doc)

    def test_a_transparent_png_stays_transparent(self):
        out = stamp_overlay.overlay_stamps(_pages(1), [_place(image=_png(color=(10, 90, 160, 70)))])
        self.assertTrue(pdf_probe.has_soft_mask(out), "the alpha channel reached the PDF")

    def test_two_stamps_are_both_drawn_each_where_it_was_placed(self):
        second_place = _place(x_mm=100.0, y_mm=100.0, width_mm=20.0, height_mm=10.0)
        out = stamp_overlay.overlay_stamps(_pages(1), [_place(), second_place])
        first, second = pdf_probe.placements(out)[0]
        self.assertPlacement(first, 10.0, 20.0, 30.0, 40.0)
        self.assertPlacement(second, 100.0, 100.0, 20.0, 10.0)

    def test_the_blank_sample_sheet_has_the_requested_size(self):
        sheet = stamp_overlay.blank_pdf(210.0, 297.0, 'sample')
        self.assertEqual([round(v) for v in stamp_overlay.page_size_mm(sheet)], [210, 297])
        self.assertEqual(pdf_probe.page_count(sheet), 1)
