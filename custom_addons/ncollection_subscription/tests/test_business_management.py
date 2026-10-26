# -*- coding: utf-8 -*-
"""The platform's application is "Management" / "الإدارة" (technical name unchanged), with an
office icon."""
from odoo.tests import TransactionCase, tagged
from odoo.tools import file_open


@tagged('post_install', '-at_install')
class TestBusinessManagementApp(TransactionCase):

    def test_the_app_is_named_management_in_english_and_arabic(self):
        self.env['res.lang']._activate_lang('ar_001')
        module = self.env['ir.module.module'].search([('name', '=', 'ncollection_subscription')])
        module._update_translations(['ar_001'])
        menu = self.env.ref('ncollection_subscription.menu_ncollection_saas_root')
        self.assertEqual(menu.with_context(lang='en_US').name, 'Management')
        self.assertEqual(menu.with_context(lang='ar_001').name, 'الإدارة')
        self.assertEqual(menu.web_icon, 'ncollection_subscription,static/description/icon_business.png')
        with file_open('ncollection_subscription/static/description/icon_business.png', 'rb') as handle:
            self.assertEqual(handle.read(8), b'\x89PNG\r\n\x1a\n')
        self.assertTrue(self.env['ir.module.module'].search([('name', '=', 'ncollection_saas')]),
                        'the technical module name is unchanged')
