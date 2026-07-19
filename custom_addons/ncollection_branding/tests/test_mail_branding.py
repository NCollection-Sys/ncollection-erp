# -*- coding: utf-8 -*-
"""Branded transactional email layout (P1-T18).

Asserts the two shared QWeb layouts every transactional email renders
through carry NCollection branding and zero Odoo references. Because
password reset / invitation / SO / invoice / PO all wrap in one of these
two layouts, branding them here brands all of them.
"""

from odoo.tests import TransactionCase, tagged

LAYOUTS = ['mail.mail_notification_layout', 'mail.mail_notification_light']


@tagged("post_install", "-at_install")
class TestMailBranding(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.ref('base.main_company')
        cls.partner = cls.env['res.partner'].create({
            'name': 'Test Customer', 'email': 'cust@example.com',
        })
        cls.message = cls.env['mail.message'].create({
            'model': 'res.partner', 'res_id': cls.partner.id,
            'body': '<p>Your document is ready.</p>',
            'message_type': 'notification', 'subject': 'Test',
        })

    def _render(self, template):
        html = self.env['ir.qweb']._render(template, {
            'message': self.message,
            'company': self.company,
            'record_name': 'Test Document',
            'record': self.partner,
            'model_description': 'Document',
            'subtitles': ['Test'],
            'show_footer': True,
            'email_notification_force_footer': True,
        })
        return str(html)

    def test_layouts_have_no_odoo_reference(self):
        for template in LAYOUTS:
            html = self._render(template).lower()
            self.assertNotIn('odoo.com', html, f"{template} still links odoo.com")
            self.assertNotIn(
                'www.odoo', html, f"{template} still references odoo",
            )

    def test_layouts_are_ncollection_branded(self):
        for template in LAYOUTS:
            html = self._render(template)
            self.assertIn('NCollection ERP', html, f"{template} missing NCollection footer")

    def test_layouts_embed_inline_base64_logo(self):
        for template in LAYOUTS:
            html = self._render(template)
            self.assertIn(
                'data:image/png;base64,', html,
                f"{template} must embed the logo as an inline base64 data URI",
            )

    def test_layouts_render_company_footer(self):
        html = self._render('mail.mail_notification_layout')
        self.assertIn(self.company.name, html)
        self.assertIn(self.company.email, html)
        self.assertIn(self.company.website, html)

    def test_branded_layout_templates_installed(self):
        for xmlid in (
            'ncollection_branding.mail_notification_layout_branded',
            'ncollection_branding.mail_notification_light_branded',
            'ncollection_branding.nc_email_logo_header',
        ):
            self.assertTrue(
                self.env.ref(xmlid, raise_if_not_found=False),
                f"{xmlid} must exist",
            )

    def test_company_email_accent_colors_set(self):
        self.assertEqual(self.company.email_secondary_color, '#1F5F8F')
