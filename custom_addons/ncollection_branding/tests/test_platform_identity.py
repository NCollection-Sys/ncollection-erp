# -*- coding: utf-8 -*-
"""The SYSTEM's identity is the platform's, everywhere a tenant shows it.

* the platform's System Branding arrives over config-sync and is applied by a whitelisted, validated write;
* the installable app (web manifest, icons) carries that identity, not Odoo's;
* what the system user writes in Discuss says the system's name, and follows a rename;
* this product's own e-mail subjects follow it too.
"""
import base64
import io

from PIL import Image

from odoo.exceptions import UserError
from odoo.tests import HttpCase, TransactionCase, tagged
from odoo.tools import file_open

from odoo.addons.ncollection_branding.models.mail_thread import renamed, system_wording


def _png(color, size=(64, 64)):
    out = io.BytesIO()
    Image.new('RGB', size, color).save(out, format='PNG')
    return base64.b64encode(out.getvalue()).decode()


@tagged('post_install', '-at_install')
class TestPlatformIdentityApplied(TransactionCase):

    def setUp(self):
        super().setUp()
        self.Brand = self.env['ncollection.product.branding']
        self.identity = {'name': 'Balance Cloud', 'short_name': 'Balance Cloud', 'browser_title': False,
                         'copyright_text': False, 'primary_color': '#123456', 'secondary_color': '#654321',
                         'logo': _png('navy'), 'login_logo': False, 'admin_logo': False, 'favicon': _png('teal')}

    def test_the_platform_identity_is_applied_and_a_second_push_changes_nothing(self):
        changed = self.Brand._nc_apply_platform_identity(self.identity)
        self.assertIn('name', changed)
        brand = self.Brand.nc_brand()
        self.assertEqual((brand['name'], brand['short_name'], brand['primary_color']),
                         ('Balance Cloud', 'Balance Cloud', '#123456'))
        self.assertEqual(self.Brand._nc_apply_platform_identity(self.identity), [], 'idempotent')
        self.assertEqual(self.env.ref('base.partner_root').name, 'Balance Cloud Bot', 'the bot follows the short name')

    def test_only_the_system_fields_are_accepted(self):
        with self.assertRaises(UserError):
            self.Brand._nc_apply_platform_identity(dict(self.identity, nc_primary_color='#000000'))
        with self.assertRaises(UserError):
            self.Brand._nc_apply_platform_identity(['not', 'a', 'dict'])

    def test_an_empty_name_never_blanks_the_system(self):
        before = self.Brand.nc_brand()['name']
        self.Brand._nc_apply_platform_identity({'name': False})
        self.assertEqual(self.Brand.nc_brand()['name'], before)

    def test_the_image_validation_of_the_form_applies_to_a_push(self):
        with self.assertRaises(Exception):
            self.Brand._nc_apply_platform_identity(dict(self.identity, logo=base64.b64encode(b'<svg/>').decode()))

    def test_the_tenant_receives_it_through_sync_from_platform_with_its_company_details(self):
        company = self.env.company
        company.write({'email': 'noreply@ncollection.com', 'website': 'https://ncollection.com', 'phone': False})
        Config = self.env['ncollection.workspace.config']
        config = Config.get_config() or Config.create({})
        answer = config.sync_from_platform({
            'nc_system_branding': self.identity,
            'nc_company_identity': {'email': 'owner@atlas.example', 'website': 'http://atlas.example.test',
                                    'phone': '+971 50 000 0000'}})
        self.assertTrue(answer['accepts_system_branding'])
        self.assertEqual(self.Brand.nc_brand()['name'], 'Balance Cloud')
        self.assertEqual((company.email, company.website, company.phone),
                         ('owner@atlas.example', 'http://atlas.example.test', '+971 50 000 0000'))

    def test_a_placeholder_the_platform_has_no_value_for_is_cleared(self):
        company = self.env.company
        company.write({'email': 'noreply@ncollection.com', 'website': 'https://ncollection.com', 'phone': False})
        written = self.env['ncollection.workspace.config']._nc_apply_company_identity(
            {'email': '', 'phone': '', 'website': 'http://atlas.example.test'})
        self.assertEqual(written, ['email', 'website'])
        self.assertEqual((company.email, company.website, company.phone),
                         (False, 'http://atlas.example.test', False), 'never an invented ncollection.com address')

    def test_the_placeholder_sender_is_removed_and_an_operators_sender_kept(self):
        params = self.env['ir.config_parameter'].sudo()
        params.set_param('mail.default.from', 'noreply@ncollection.com')
        params.set_param('mail.default.from_filter', 'billing.example.test')
        self.env['ncollection.workspace.config']._nc_apply_company_identity({'email': 'a@b.example'})
        self.assertFalse(params.get_param('mail.default.from'), 'no email leaves "from noreply@ncollection.com"')
        self.assertEqual(params.get_param('mail.default.from_filter'), 'billing.example.test')

    def test_company_details_the_owner_entered_are_never_replaced(self):
        company = self.env.company
        company.write({'email': 'mine@company.example', 'website': False})
        written = self.env['ncollection.workspace.config']._nc_apply_company_identity(
            {'email': 'platform@x.example', 'website': 'http://x.example.test'})
        self.assertEqual(written, ['website'])
        self.assertEqual(company.email, 'mine@company.example')
        with self.assertRaises(UserError):
            self.env['ncollection.workspace.config']._nc_apply_company_identity({'name': 'Hijack'})

    def test_the_company_screen_shows_the_system_logo_and_leaves_the_company_logo_alone(self):
        company = self.env.company
        own = company.logo
        self.Brand._nc_apply_platform_identity(self.identity)
        self.assertEqual(base64.b64decode(company.nc_system_logo), base64.b64decode(self.identity['logo']))
        self.assertEqual(company.logo, own)

    # Who may change System Branding inside a tenant (not its Owner) is pinned by test_system_branding_boundary.py;
    # this push changes nothing about that boundary: it is applied only through sync_from_platform.


@tagged('post_install', '-at_install')
class TestSystemWording(TransactionCase):

    def test_the_vendor_names_become_the_system_names(self):
        body = "Hello,<br/>Odoo's chat helps employees. Write @OdooBot. Enjoy exploring Odoo! https://www.odoo.com"
        worded = system_wording(body, 'Balance Cloud', 'Balance Cloud Bot')
        self.assertNotIn('Odoo', worded)
        self.assertIn("Balance Cloud's chat", worded)
        self.assertIn('@Balance Cloud Bot', worded)
        self.assertIn('https://www.odoo.com', worded, 'addresses are lower-case and untouched')
        self.assertEqual(system_wording(worded, 'Balance Cloud', 'Balance Cloud Bot'), worded)
        self.assertEqual(renamed('NCollection ERP\'s chat, @NCollection Bot', 'NCollection ERP', 'Balance Cloud',
                                 'NCollection Bot', 'Balance Cloud Bot'), "Balance Cloud's chat, @Balance Cloud Bot")

    def test_a_message_the_system_user_writes_says_the_system_name_and_follows_a_rename(self):
        root = self.env.ref('base.partner_root')
        partner = self.env['res.partner'].create({'name': 'Wording'})
        message = partner.message_post(body="Odoo's chat helps employees", author_id=root.id,
                                       message_type='comment')
        name = self.env['ncollection.product.branding'].nc_brand()['name']
        self.assertIn(name, str(message.body))
        self.assertNotIn('Odoo', str(message.body))
        person = self.env['res.users'].create({'name': 'A Person', 'login': 'pi_person',
                                               'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        other = self.env['mail.message'].create({
            'model': 'res.partner', 'res_id': partner.id, 'author_id': person.partner_id.id,
            'body': "Odoo is mentioned by a person", 'message_type': 'comment'})
        self.assertIn('Odoo', str(other.body), 'a person\'s own words are never rewritten')
        self.env['ncollection.product.branding'].with_user(self.env.ref('base.user_root'))._nc_record().write(
            {'name': 'Renamed System'})
        self.assertIn('Renamed System', str(message.body))
        self.assertIn('Odoo', str(other.body))

    def test_this_products_mail_subjects_say_the_system_name(self):
        from ..models.vendor_content import SUBJECT_RULES, _apply
        subject = _apply(SUBJECT_RULES, 'Your NCollection subscription has expired')
        self.assertNotIn('NCollection', subject)
        self.assertIn("nc_brand()['name']", subject)
        self.assertEqual(_apply(SUBJECT_RULES, subject), subject, 'idempotent')


@tagged('post_install', '-at_install')
class TestInstallableAppIdentity(HttpCase):

    def test_the_web_manifest_is_the_system_not_odoo(self):
        brand = self.env['ncollection.product.branding'].nc_brand()
        manifest = self.url_open('/web/manifest.webmanifest').json()
        self.assertEqual((manifest['name'], manifest['short_name']), (brand['name'], brand['short_name']))
        self.assertNotIn('Odoo', str(manifest))
        self.assertNotIn('odoo-icon', str(manifest))
        self.assertEqual(manifest['theme_color'], brand['primary_color'])
        self.assertEqual([icon['sizes'] for icon in manifest['icons']], ['192x192', '512x512'])
        for icon in manifest['icons']:
            response = self.url_open(icon['src'])
            self.assertEqual(response.headers['Content-Type'], 'image/png')
            size = int(icon['sizes'].split('x')[0])
            self.assertEqual(Image.open(io.BytesIO(response.content)).size, (size, size))
        self.assertEqual(self.url_open('/ncollection/brand/app-icon/64').status_code, 404, 'only the declared sizes')

    def test_a_browser_notification_without_author_shows_the_system_icon(self):
        partner = self.env['res.partner'].create({'name': 'Push'})
        message = partner.message_post(body='Hello', message_type='comment', author_id=False)
        payload = partner._notify_by_web_push_prepare_payload(message, msg_vals={'author_id': False})
        self.assertTrue(payload['options']['icon'].startswith('/ncollection/brand/app-icon/192'))

    def test_the_offline_page_names_the_system(self):
        with file_open('ncollection_branding/views/webclient_templates.xml') as handle:
            self.assertIn('webclient_offline_branded', handle.read())
