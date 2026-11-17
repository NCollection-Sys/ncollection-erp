# -*- coding: utf-8 -*-
"""Purchase Settings in the unified Settings (the shared checks live in ncollection_sale_documents' test)."""
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestPurchaseSettingsSection(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        company = cls.env.company
        common = dict(company_id=company.id, company_ids=[(6, 0, company.ids)])
        cls.admin = new_test_user(cls.env, login='sec_admin_p',
                                  groups='base.group_user,ncollection_core.group_tenant_admin', **common)
        cls.delegate = new_test_user(cls.env, login='sec_delegate_p',
                                     groups='base.group_user,ncollection_core.group_settings_sales', **common)

    def _page(self, user, section, **vals):
        return self.env['ncollection.business.settings'].with_user(user).with_context(
            nc_settings_section=section).create(vals)

    def test_purchase_section(self):
        self._page(self.admin, 'purchase', lock_confirmed_po=True, group_warning_purchase=True).action_nc_save()
        self.assertEqual(self.env.company.po_lock, 'lock')
        again = self._page(self.admin, 'purchase')
        self.assertTrue(again.lock_confirmed_po)
        self.assertTrue(again.group_warning_purchase)
        with self.assertRaises(AccessError):                # a Sales delegate is not a Purchase delegate
            self._page(self.delegate, 'purchase')
