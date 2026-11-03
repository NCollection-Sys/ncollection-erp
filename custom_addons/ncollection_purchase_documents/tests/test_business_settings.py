# -*- coding: utf-8 -*-
"""Option A: the workspace Owner changes the purchase business settings through the Workspace Administration Center;
"Lock Confirmed Orders" and "Purchase Order Approval" become the company's po_lock / po_double_validation exactly as
Odoo's own settings page does it."""
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestPurchaseBusinessSettings(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        company = cls.env.company
        cls.owner = new_test_user(cls.env, login='bsp_owner',
                                  groups='base.group_user,ncollection_core.group_role_owner',
                                  company_id=company.id, company_ids=[(6, 0, company.ids)])

    def _settings(self, **vals):
        return self.env['ncollection.business.settings'].with_user(self.owner).create(vals)

    def test_purchase_settings_are_saved_where_odoo_keeps_them(self):
        company = self.env.company
        company.write({'po_lock': 'edit', 'po_double_validation': 'one_step'})
        self._settings(lock_confirmed_po=True, po_order_approval=True, po_double_validation_amount=12000.0,
                       group_warning_purchase=True).action_nc_save()
        self.assertEqual(company.po_lock, 'lock')
        self.assertEqual(company.po_double_validation, 'two_step')
        self.assertEqual(company.po_double_validation_amount, 12000.0)
        self.assertIn(self.env.ref('purchase.group_warning_purchase'), self.env.ref('base.group_user').implied_ids)
        again = self._settings()
        self.assertTrue(again.lock_confirmed_po and again.po_order_approval)
        self._settings(lock_confirmed_po=False, po_order_approval=False).action_nc_save()
        self.assertEqual((company.po_lock, company.po_double_validation), ('edit', 'one_step'))
