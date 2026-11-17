# -*- coding: utf-8 -*-
"""Inventory Settings in the unified Settings (the shared checks live in ncollection_sale_documents' test)."""
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestInventorySettingsSection(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        company = cls.env.company
        common = dict(company_id=company.id, company_ids=[(6, 0, company.ids)])
        cls.admin = new_test_user(cls.env, login='sec_admin_i',
                                  groups='base.group_user,ncollection_core.group_tenant_admin', **common)
        cls.delegate = new_test_user(cls.env, login='sec_delegate_i',
                                     groups='base.group_user,ncollection_core.group_settings_sales', **common)

    def _page(self, user, section, **vals):
        return self.env['ncollection.business.settings'].with_user(user).with_context(
            nc_settings_section=section).create(vals)

    def test_inventory_section(self):
        self._page(self.admin, 'inventory', group_stock_tracking_lot=True, annual_inventory_day=15).action_nc_save()
        page = self._page(self.admin, 'inventory')
        self.assertTrue(page.group_stock_tracking_lot)
        self.assertEqual(page.annual_inventory_day, 15)
        self.assertEqual(self.env.company.annual_inventory_day, 15)
        self.assertIn(self.env.ref('stock.group_tracking_lot'), self.env.ref('base.group_user').implied_ids)
        with self.assertRaises(AccessError):
            self._page(self.delegate, 'inventory')

    def test_lots_cannot_be_switched_off_with_tracked_products(self):
        self._page(self.admin, 'inventory', group_stock_production_lot=True).action_nc_save()
        self.env['product.product'].create({'name': 'Tracked', 'is_storable': True, 'tracking': 'lot'})
        with self.assertRaises(UserError):
            self._page(self.admin, 'inventory', group_stock_production_lot=False).action_nc_save()
