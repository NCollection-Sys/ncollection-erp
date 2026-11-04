# -*- coding: utf-8 -*-
"""The Sales / Invoicing analysis actions and menus exist and are gated to the roles the RPC admits."""
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestPremiumMenus(TransactionCase):

    def test_the_actions_and_menus_exist_and_are_gated(self):
        for xmlid in ('ncollection_account_dashboard.action_sales_premium',
                      'ncollection_account_dashboard.action_invoicing_premium'):
            self.assertEqual(self.env.ref(xmlid).type, 'ir.actions.client')
        for xmlid in ('menu_sales_premium', 'menu_invoicing_premium', 'menu_sales_premium_dept'):
            menu = self.env.ref('ncollection_account_dashboard.' + xmlid)
            self.assertTrue(menu.group_ids, "%s is gated to roles" % xmlid)
