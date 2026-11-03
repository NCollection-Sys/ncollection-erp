# -*- coding: utf-8 -*-
"""Option A: the workspace Owner changes the sales business settings through the Workspace Administration Center;
each lands where Odoo's own settings page stores it (company field, feature group, configuration parameter)."""
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestSalesBusinessSettings(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        company = cls.env.company
        cls.owner = new_test_user(cls.env, login='bss_owner',
                                  groups='base.group_user,ncollection_core.group_role_owner',
                                  company_id=company.id, company_ids=[(6, 0, company.ids)])

    def _settings(self, **vals):
        return self.env['ncollection.business.settings'].with_user(self.owner).create(vals)

    def test_sales_settings_are_saved_where_odoo_keeps_them(self):
        proforma = self.env.ref('sale.group_proforma_sales')
        self._settings(quotation_validity_days=21, portal_confirmation_pay=True, group_proforma_sales=True,
                       automatic_invoice=True).action_nc_save()
        company = self.env.company
        self.assertEqual(company.quotation_validity_days, 21)
        self.assertTrue(company.portal_confirmation_pay)
        self.assertIn(proforma, self.env.ref('base.group_user').implied_ids)
        self.assertEqual(self.env['ir.config_parameter'].sudo().get_param('sale.automatic_invoice'), 'True')
        again = self._settings()
        self.assertEqual(again.quotation_validity_days, 21)
        self.assertTrue(again.group_proforma_sales)

    def test_a_negative_validity_is_refused(self):
        with self.assertRaises(ValidationError):
            self._settings(quotation_validity_days=-3).action_nc_save()
