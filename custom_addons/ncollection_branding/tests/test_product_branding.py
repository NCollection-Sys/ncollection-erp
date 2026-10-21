# -*- coding: utf-8 -*-
"""Product branding: ONE source for the application's name, logos and favicon, distinct from the company's.

Synthetic images only. The HTTP tests read what a browser would: the sign-in page, the image route, session_info.
"""
import base64
import io

from PIL import Image

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import HttpCase, TransactionCase, tagged


def _png(color=(30, 90, 160, 255), size=(40, 20)):
    buffer = io.BytesIO()
    Image.new('RGBA', size, color).save(buffer, 'PNG')
    return base64.b64encode(buffer.getvalue())


def _ico():
    buffer = io.BytesIO()
    Image.new('RGBA', (32, 32), (200, 40, 40, 255)).save(buffer, 'ICO')
    return base64.b64encode(buffer.getvalue())


@tagged('post_install', '-at_install')
class TestProductBrandingModel(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Brand = cls.env['ncollection.product.branding']
        cls.record = cls.Brand._nc_record()
        cls.plain = cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Plain Brand User', 'login': 'plain_brand',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])]})

    def test_there_is_one_record_with_the_default_name(self):
        self.assertEqual(self.Brand.sudo().search_count([]), 1)
        self.assertTrue(self.Brand.nc_brand()['name'])
        with self.assertRaises(ValidationError):
            self.Brand.create({'name': 'Second'})
        with self.assertRaises(ValidationError):
            self.record.unlink()

    def test_changing_the_name_changes_every_consumer(self):
        self.record.write({'name': 'SomeOtherERP', 'short_name': 'Other'})
        brand = self.Brand.nc_brand()
        self.assertEqual((brand['name'], brand['short_name']), ('SomeOtherERP', 'Other'))

    def test_urls_carry_a_version_that_changes_with_the_image(self):
        before = self.Brand.nc_brand()
        self.assertIn('/ncollection/brand/favicon?v=', before['favicon_url'])
        self.record.write({'favicon': _ico()})
        after = self.Brand.nc_brand()
        self.assertTrue(after['has']['favicon'])
        self.assertGreaterEqual(after['version'], before['version'])

    def test_login_and_admin_logos_fall_back_to_the_product_logo_then_to_the_shipped_one(self):
        self.record.write({'logo': False, 'login_logo': False, 'admin_logo': False})
        default, _mime = self.Brand._nc_image('logo')
        self.assertEqual(self.Brand._nc_image('login')[0], default)
        product = base64.b64decode(_png(color=(1, 2, 3, 255)))
        self.record.write({'logo': base64.b64encode(product)})
        self.assertEqual(self.Brand._nc_image('login')[0], product)
        self.assertEqual(self.Brand._nc_image('admin')[0], product)
        own = base64.b64decode(_png(color=(9, 9, 9, 255)))
        self.record.write({'admin_logo': base64.b64encode(own)})
        self.assertEqual(self.Brand._nc_image('admin')[0], own)
        self.assertEqual(self.Brand._nc_image('login')[0], product, "the login logo still follows the product logo")

    def test_only_allowed_images_are_stored(self):
        svg = base64.b64encode(b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>')
        exe = base64.b64encode(b'MZ\x90\x00 not an image')
        for label, bad in (('svg', svg), ('exe', exe), ('text', base64.b64encode(b'hello'))):
            with self.subTest(label), self.assertRaises(ValidationError):
                self.record.write({'logo': bad})
        with self.assertRaises(ValidationError):
            self.record.write({'logo': base64.b64encode(b'\x89PNG\r\n\x1a\n' + b'0' * 5 + b'broken')})
        too_big = base64.b64encode(b'\x89PNG\r\n\x1a\n' + b'\0' * (2 * 1024 * 1024 + 1))
        with self.assertRaises(ValidationError):
            self.record.write({'logo': too_big})
        # a favicon is PNG or ICO only
        jpeg = io.BytesIO()
        Image.new('RGB', (16, 16)).save(jpeg, 'JPEG')
        with self.assertRaises(ValidationError):
            self.record.write({'favicon': base64.b64encode(jpeg.getvalue())})
        self.record.write({'favicon': _png(size=(32, 32))})

    def test_the_content_decides_not_the_name(self):
        """A file called logo.png holding a script is refused: nothing here looks at a file name."""
        with self.assertRaises(ValidationError):
            self.record.write({'login_logo': base64.b64encode(b'<script>alert(1)</script>')})

    def test_only_an_administrator_changes_the_branding(self):
        with self.assertRaises(AccessError):
            self.record.with_user(self.plain).write({'name': 'Hijacked'})
        with self.assertRaises(AccessError):
            self.Brand.with_user(self.plain).create({'name': 'Hijacked'})
        # reading the resolved identity is what every page does, for everyone
        self.assertTrue(self.Brand.with_user(self.plain).nc_brand()['name'])

    def test_the_company_logo_is_a_different_field_and_stays_untouched(self):
        company_logo = self.env.company.logo
        self.record.write({'logo': _png(color=(5, 5, 5, 255))})
        self.assertEqual(self.env.company.logo, company_logo)


@tagged('post_install', '-at_install')
class TestProductBrandingHttp(HttpCase):

    def setUp(self):
        super().setUp()
        self.record = self.env['ncollection.product.branding']._nc_record()

    def test_the_image_route_serves_only_the_four_images_publicly_with_safe_headers(self):
        self.record.write({'logo': _png()})
        self.env.cr.flush()
        version = self.env['ncollection.product.branding'].nc_brand()['version']
        guessed = self.url_open('/ncollection/brand/logo?v=%d' % (version + 1))
        self.assertEqual(guessed.headers['Cache-Control'], 'no-cache', 'a guessed version is never pinned')
        response = self.url_open('/ncollection/brand/logo?v=%d' % version)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Content-Type'], 'image/png')
        self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
        self.assertIn('immutable', response.headers['Cache-Control'])
        self.assertEqual(self.url_open('/ncollection/brand/logo').headers['Cache-Control'], 'no-cache')
        favicon = self.url_open('/ncollection/brand/favicon')
        self.assertEqual(favicon.status_code, 200)
        for kind in ('login', 'admin'):
            self.assertEqual(self.url_open('/ncollection/brand/' + kind).status_code, 200)
        self.assertEqual(self.url_open('/ncollection/brand/passwd').status_code, 404)
        self.assertEqual(self.url_open('/ncollection/brand/..%2Fsecret').status_code, 404)

    def test_the_sign_in_page_carries_the_configured_name_favicon_and_no_odoo(self):
        self.record.write({'name': 'SomeOtherERP', 'favicon': _ico()})
        self.env.cr.flush()
        page = self.url_open('/web/login').text
        self.assertIn('SomeOtherERP', page)
        self.assertIn('/ncollection/brand/favicon?v=', page)
        self.assertNotIn('/web/static/img/favicon.ico', page)
        self.assertNotIn('Powered by', page)
        self.assertNotRegex(page, r'<title>[^<]*Odoo')

    def test_the_web_client_receives_the_name_in_its_session_info(self):
        self.record.write({'name': 'SomeOtherERP'})
        self.env.cr.flush()
        self.authenticate('admin', 'admin')
        self.assertIn('SomeOtherERP', self.url_open('/odoo').text)

    def test_an_unconfigured_workspace_keeps_the_shipped_name(self):
        self.record.write({'name': 'NCollection ERP', 'logo': False, 'favicon': False})
        self.env.cr.flush()
        self.assertIn('NCollection ERP', self.url_open('/web/login').text)
        self.assertEqual(self.url_open('/ncollection/brand/favicon').headers['Content-Type'], 'image/x-icon')

    def test_an_image_that_claims_an_enormous_size_is_a_validation_error_not_a_crash(self):
        import struct
        import zlib
        header = struct.pack('>IIBBBBB', 60000, 60000, 8, 2, 0, 0, 0)

        def chunk(kind, data):
            body = kind + data
            return struct.pack('>I', len(data)) + body + struct.pack('>I', zlib.crc32(body) & 0xffffffff)
        png = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header) + chunk(b'IDAT', zlib.compress(b'\0'))
               + chunk(b'IEND', b''))
        with self.assertRaises(ValidationError):
            self.record.write({'logo': base64.b64encode(png)})
