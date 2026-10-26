# -*- coding: utf-8 -*-
"""Print Settings on the document types, the paper a document prints on, and the system's name in the user's
notification preference and in the web client's own product-naming strings."""
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.ncollection_branding.models.ir_http import WEB_IDENTITY_TERMS, system_named


@tagged('post_install', '-at_install')
class TestPrintSettings(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Report = cls.env['ir.actions.report']
        cls.report_a = Report.create({'name': 'Doc A', 'model': 'res.partner', 'report_name': 'base.report_a'})
        cls.report_b = Report.create({'name': 'Doc B', 'model': 'res.partner', 'report_name': 'base.report_b'})
        cls.manager_group = cls.env['res.groups'].create({'name': 'Doc A managers'})
        Type = cls.env['ncollection.document.type']
        cls.kind_a = Type.create({'name': 'Kind A', 'code': 'kind_a', 'report_ids': [(6, 0, cls.report_a.ids)],
                                  'manager_group_ids': [(6, 0, cls.manager_group.ids)]})
        cls.kind_a2 = Type.create({'name': 'Kind A2', 'code': 'kind_a2', 'report_ids': [(6, 0, cls.report_a.ids)],
                                   'manager_group_ids': [(6, 0, cls.manager_group.ids)]})
        cls.kind_b = Type.create({'name': 'Kind B', 'code': 'kind_b', 'report_ids': [(6, 0, cls.report_b.ids)]})
        cls.letter = cls.env['report.paperformat'].create({'name': 'US Letter test', 'format': 'Letter'})
        cls.manager = new_test_user(cls.env, login='print.manager', groups='base.group_user')
        cls.manager.group_ids |= cls.manager_group | cls.env.ref('ncollection_branding.group_stamp_manager')
        cls.clerk = new_test_user(cls.env, login='print.clerk', groups='base.group_user')

    def test_the_document_type_paper_wins_over_the_company_paper(self):
        self.kind_a.paperformat_id = self.letter
        self.assertEqual(self.report_a.get_paperformat(), self.letter)

    def test_a_report_that_declares_its_paper_keeps_it(self):
        own = self.env['report.paperformat'].create({'name': 'Cheque test', 'format': 'custom',
                                                     'page_width': 200, 'page_height': 90})
        self.report_a.paperformat_id = own
        self.kind_a.paperformat_id = self.letter
        self.assertEqual(self.report_a.get_paperformat(), own)

    def test_the_letterhead_paper_is_used_when_nothing_is_chosen(self):
        company = self.env.company
        company.external_report_layout_id = self.env.ref('ncollection_branding.external_layout_nc')
        self.assertEqual(self.report_b.get_paperformat(), self.env.ref('ncollection_branding.paperformat_nc_document'))
        company.external_report_layout_id = self.env.ref('web.external_layout_boxed')
        self.assertEqual(self.report_b.get_paperformat(), company.paperformat_id,
                         "another layout keeps the company's paper: its header is not the letterhead's")

    def test_kinds_printed_by_the_same_report_share_their_paper(self):
        self.kind_a.with_user(self.manager).write({'paperformat_id': self.letter.id})
        self.assertEqual(self.kind_a2.paperformat_id, self.letter)
        self.assertFalse(self.kind_b.paperformat_id)

    def test_only_a_manager_of_the_kind_changes_its_print_settings(self):
        with self.assertRaises(AccessError):
            self.kind_a.with_user(self.clerk).write({'paperformat_id': self.letter.id})
        with self.assertRaises(AccessError):
            self.kind_b.with_user(self.manager).write({'paperformat_id': self.letter.id})

    def test_nothing_but_the_print_settings_can_be_changed(self):
        with self.assertRaises(AccessError):
            self.kind_a.with_user(self.manager).write({'record_domain': "[('id', '=', 0)]"})
        with self.assertRaises(AccessError):
            self.kind_a.with_user(self.manager).write({'manager_group_ids': [(4, self.env.ref('base.group_user').id)]})


@tagged('post_install', '-at_install')
class TestSystemNameInTheClient(TransactionCase):

    def _rename(self, name):
        self.env['ncollection.product.branding'].with_user(self.env.ref('base.user_root'))._nc_record().write(
            {'name': name})

    def test_the_notification_preference_says_the_system_name_and_follows_a_rename(self):
        self._rename('Balance Cloud')
        Users = self.env['res.users']
        labels = dict(Users.fields_get(['notification_type'])['notification_type']['selection'])
        self.assertEqual(labels['inbox'], 'In Balance Cloud')
        self.assertNotIn('Odoo', Users.fields_get(['notification_type'])['notification_type']['help'])
        self._rename('Other Cloud')
        labels = dict(Users.fields_get(['notification_type'])['notification_type']['selection'])
        self.assertEqual(labels['inbox'], 'In Other Cloud')
        self.assertEqual(set(labels), {'email', 'inbox'}, 'the stored values never change')

    def test_the_notification_preference_in_arabic(self):
        self._rename('Balance Cloud')
        self.env['res.lang']._activate_lang('ar_001')
        labels = dict(self.env['res.users'].with_context(lang='ar_001').fields_get(
            ['notification_type'])['notification_type']['selection'])
        self.assertEqual(labels['inbox'], 'في Balance Cloud')

    def test_the_web_client_strings_that_name_the_product_say_the_system_name(self):
        self._rename('Balance Cloud')
        modules = list(WEB_IDENTITY_TERMS)
        for lang in ('en_US', 'ar_001'):
            self.env['res.lang']._activate_lang(lang)
            translations, _params = self.env['ir.http']._get_translations_for_webclient(modules, lang)
            for module, terms in WEB_IDENTITY_TERMS.items():
                messages = {row['id']: row['string'] for row in translations[module]['messages']}
                for source in terms:
                    with self.subTest(lang=lang, source=source):
                        self.assertIn('Balance Cloud', messages[source])
                        self.assertNotIn('Odoo', messages[source])
                        self.assertNotIn('أودو', messages[source])

    def test_only_the_product_name_is_replaced(self):
        self.assertEqual(system_named('Install Odoo', 'Balance Cloud'), 'Install Balance Cloud')
        self.assertEqual(system_named('تثبيت أودو', 'Balance Cloud'), 'تثبيت Balance Cloud')
        self.assertEqual(system_named('المنبثقة لأودو.', 'Balance Cloud'), 'المنبثقة لـ Balance Cloud.')
        self.assertEqual(system_named('Odoobot', 'X'), 'Odoobot', 'a word that merely contains it is left alone')
