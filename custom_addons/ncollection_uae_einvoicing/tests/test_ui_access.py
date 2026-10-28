# -*- coding: utf-8 -*-
"""The operational workflow and who may use it. Every restriction is tested at the ORM (a direct call as the user),
never only through a view: a hidden button or menu is not a control."""

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import GROUP, EInvoiceCase


@tagged('post_install', '-at_install')
class TestOperationalWorkflow(EInvoiceCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        accounting = 'account.group_account_invoice'
        cls.user = cls._user('ui.user', [GROUP + 'group_einvoice_user', accounting])
        cls.manager = cls._user('ui.manager', [GROUP + 'group_einvoice_manager', accounting])
        cls.outsider = cls._user('ui.outsider', [accounting])

    def test_the_smart_button_creates_once_then_reopens_the_same_einvoice(self):
        move = self._invoice()
        first = move.with_user(self.user).action_nc_open_einvoice()
        again = move.with_user(self.user).action_nc_open_einvoice()
        self.assertEqual(first['res_model'], 'ncollection.einvoice')
        self.assertEqual(first['res_id'], again['res_id'])
        self.assertEqual(move.with_user(self.user).nc_einvoice_count, 1)

    def test_an_outsider_cannot_open_or_read_einvoices_at_the_orm(self):
        move = self._invoice()
        with self.assertRaises(AccessError):
            move.with_user(self.outsider).action_nc_open_einvoice()
        record = self._einvoice(move)
        with self.assertRaises(AccessError):
            record.with_user(self.outsider).read(['name'])

    def test_the_button_refuses_a_vendor_bill_through_the_models_own_guard(self):
        bill = self.env['account.move'].create(
            {
                'move_type': 'in_invoice',
                'partner_id': self.buyer.id,
                'company_id': self.company.id,
                'invoice_date': '2026-10-01',
                'invoice_line_ids': [Command.create({'name': 'x', 'quantity': 1, 'price_unit': 10.0})],
            }
        )
        bill.action_post()
        with self.assertRaisesRegex(UserError, 'vendor bill'):
            bill.with_user(self.user).action_nc_open_einvoice()

    def test_the_full_user_flow_prepare_generate_then_validation_needs_the_validator(self):
        move = self._invoice()
        action = move.with_user(self.user).action_nc_open_einvoice()
        record = self.env['ncollection.einvoice'].with_user(self.user).browse(action['res_id'])
        self.assertEqual(record.ui_status, 'draft')
        record.action_prepare()
        self.assertEqual(record.ui_status, 'ready')
        record.action_generate_xml()
        self.assertEqual(record.ui_status, 'ready', "generated XML is not validated")
        # no validator configured in this environment: validation is recorded as unavailable, never as validated
        result = record.action_validate()
        self.assertEqual(result['params']['type'], 'warning')
        self.assertEqual(record.ui_status, 'ready')
        self.assertEqual(record.submission_state, 'not_submitted')

    def test_the_status_has_exactly_five_values_and_none_says_transmitted(self):
        values = dict(self.env['ncollection.einvoice']._fields['ui_status'].selection)
        self.assertEqual(set(values), {'not_required', 'draft', 'ready', 'invalid', 'validated'})
        for label in values.values():
            self.assertNotRegex(label.lower(), 'sent|transmit|submit|deliver|report')

    def test_status_mapping(self):
        record = self._einvoice()
        self.assertEqual(record.ui_status, 'draft')
        buyer = self._partner('Bare Buyer LLC')
        blocked = self._einvoice(self._invoice(partner=buyer))
        blocked.action_prepare()
        self.assertEqual(blocked.ui_status, 'invalid', "blocking findings")
        consumer = self._partner('Private Person')
        consumer.nc_einvoice_party_type = 'consumer'
        excluded = self._einvoice(self._invoice(partner=consumer))
        excluded.action_prepare()
        self.assertEqual(excluded.ui_status, 'not_required')
        self.assertEqual(self._validated(record).ui_status, 'validated')

    def test_configuration_is_manager_only_at_the_orm(self):
        Map = self.env['ncollection.einvoice.tax.map']
        with self.assertRaises(AccessError):
            Map.with_user(self.user).create({'company_id': self.company.id, 'tax_id': self.tax_s.id,
                                             'pint_category': 'S'})
        with self.assertRaises(AccessError):
            self.config.with_user(self.user).write({'enabled': False})
        holiday = self.env['ncollection.einvoice.holiday'].with_user(self.manager).create(
            {'date': '2026-12-02', 'name': 'National Day'}
        )
        with self.assertRaises(AccessError):
            holiday.with_user(self.user).write({'name': 'x'})
        self.assertTrue(holiday.with_user(self.user).read(['name']))

    def test_menus_follow_the_groups(self):
        Menu = self.env['ir.ui.menu']
        root = self.env.ref('ncollection_uae_einvoicing.menu_einvoice_root')
        configuration = self.env.ref('ncollection_uae_einvoicing.menu_einvoice_configuration')
        user_menus = Menu.with_user(self.user)._visible_menu_ids()
        manager_menus = Menu.with_user(self.manager)._visible_menu_ids()
        outsider_menus = Menu.with_user(self.outsider)._visible_menu_ids()
        self.assertIn(root.id, user_menus)
        self.assertNotIn(configuration.id, user_menus)
        self.assertIn(configuration.id, manager_menus)
        self.assertNotIn(root.id, outsider_menus)

    def test_the_views_load_for_a_user(self):
        for model in ('ncollection.einvoice', 'ncollection.einvoice.config', 'ncollection.einvoice.tax.map'):
            views = self.env[model].with_user(self.user).get_views([(False, 'list'), (False, 'form')])
            self.assertIn('list', views['views'])
