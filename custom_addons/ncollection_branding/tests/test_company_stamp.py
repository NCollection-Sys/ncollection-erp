# -*- coding: utf-8 -*-
"""The company stamp image: a validated PNG per company, stored as uploaded (the default of the document stamp system).

Synthetic images only. The stamp is a Binary kept byte-for-byte: a transparent seal must stay transparent.
"""
import base64
import io

from PIL import Image

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged

from ..models.res_company import NC_STAMP_MAX_BYTES, NC_STAMP_MAX_SIDE_PX


def _png(size=(120, 80), color=(200, 30, 30, 160)):
    buffer = io.BytesIO()
    Image.new('RGBA', size, color).save(buffer, 'PNG')
    return buffer.getvalue()


def _image(fmt, size=(40, 40)):
    buffer = io.BytesIO()
    Image.new('RGB', size, (10, 20, 30)).save(buffer, fmt)
    return buffer.getvalue()


def _b64(raw):
    return base64.b64encode(raw)


@tagged('post_install', '-at_install')
class TestCompanyStamp(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company_a = cls.env['res.company'].create({'name': 'Stamp Co A'})
        cls.company_b = cls.env['res.company'].create({'name': 'Stamp Co B'})
        cls.plain = cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'stamp_plain', 'login': 'stamp_plain', 'company_id': cls.company_a.id,
            'company_ids': [(6, 0, cls.company_a.ids)],
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])]})

    # ------------------------------------------------------------------ upload, keep, replace, remove
    def test_a_transparent_png_is_stored_exactly_as_uploaded(self):
        raw = _png()
        self.company_a.nc_stamp = _b64(raw)
        stored = base64.b64decode(self.company_a.nc_stamp)
        self.assertEqual(stored, raw, "not re-encoded, not resized")
        image = Image.open(io.BytesIO(stored))
        self.assertEqual((image.format, image.mode, image.size), ('PNG', 'RGBA', (120, 80)))
        self.assertLess(image.getpixel((0, 0))[3], 255, "the alpha channel survived")

    def test_replace_and_remove(self):
        first, second = _png((50, 50)), _png((70, 30), (0, 0, 200, 255))
        self.company_a.nc_stamp = _b64(first)
        self.company_a.nc_stamp = _b64(second)
        self.assertEqual(base64.b64decode(self.company_a.nc_stamp), second)
        self.company_a.nc_stamp = False
        self.assertFalse(self.company_a.nc_stamp)

    def test_only_a_real_png_is_accepted(self):
        bad = {
            'jpeg': _image('JPEG'), 'gif': _image('GIF'), 'bmp': _image('BMP'),
            'svg': b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
            'text': b'not an image at all',
            'png signature, garbage body': b'\x89PNG\r\n\x1a\n' + b'garbage' * 20,
            'truncated png': _png()[:40],
        }
        for label, raw in bad.items():
            with self.subTest(label), self.assertRaises(ValidationError):
                self.company_a.nc_stamp = _b64(raw)
        self.assertFalse(self.company_a.nc_stamp)

    def test_size_limits(self):
        with self.assertRaises(ValidationError):
            self.company_a.nc_stamp = _b64(_png((NC_STAMP_MAX_SIDE_PX + 1, 10)))
        too_big = b'\x89PNG\r\n\x1a\n' + b'\0' * (NC_STAMP_MAX_BYTES + 1)
        with self.assertRaises(ValidationError):
            self.company_a.nc_stamp = _b64(too_big)

    def test_not_valid_base64_is_refused(self):
        with self.assertRaises(ValidationError):
            self.company_a.nc_stamp = b'%%%not-base64%%%'

    # ------------------------------------------------------------------ per company
    def test_each_company_keeps_its_own_stamp(self):
        a, b = _png((60, 40), (255, 0, 0, 255)), _png((60, 40), (0, 0, 255, 255))
        self.company_a.nc_stamp = _b64(a)
        self.company_b.nc_stamp = _b64(b)
        self.assertEqual(base64.b64decode(self.company_a.nc_stamp), a)
        self.assertEqual(base64.b64decode(self.company_b.nc_stamp), b)
        self.company_a.nc_stamp = False
        self.assertEqual(base64.b64decode(self.company_b.nc_stamp), b, "removing A's stamp leaves B's")

    # ------------------------------------------------------------------ rights
    def test_an_ordinary_user_cannot_change_the_stamp(self):
        with self.assertRaises(AccessError):
            self.company_a.with_user(self.plain).write({'nc_stamp': _b64(_png())})
        self.assertFalse(self.company_a.nc_stamp)
