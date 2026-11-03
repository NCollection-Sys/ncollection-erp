# -*- coding: utf-8 -*-
"""Option A: the workspace Owner changes the accounting business settings and Accounting Defaults through the
Workspace Administration Center - applied where Odoo stores them, validated, own company only - while Odoo's General
Settings, a field outside the allowlist and another company's records stay refused."""
from odoo.exceptions import AccessError, ValidationError
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.tests import new_test_user, tagged


@tagged('post_install', '-at_install')
class TestAccountingBusinessSettings(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        company = cls.env.company
        common = {'company_id': company.id, 'company_ids': [(6, 0, company.ids)]}
        cls.owner = new_test_user(
            cls.env, login='bs_owner',
            groups='base.group_user,ncollection_core.group_role_owner,account.group_account_user', **common)
        cls.clerk = new_test_user(cls.env, login='bs_clerk', groups='base.group_user,account.group_account_user',
                                  **common)
        cls.sale_tax = cls.env['account.tax'].create({'name': 'BS Sale 7', 'amount': 7, 'type_tax_use': 'sale'})
        cls.purchase_tax = cls.env['account.tax'].create(
            {'name': 'BS Purchase 7', 'amount': 7, 'type_tax_use': 'purchase'})

    def _settings(self, user=None, **vals):
        return self.env['ncollection.business.settings'].with_user(user or self.owner).create(vals)

    def test_the_owner_changes_accounting_settings_and_they_persist(self):
        self.env.company.qr_code = False
        self._settings(sale_tax_id=self.sale_tax.id, purchase_tax_id=self.purchase_tax.id, qr_code=True,
                       tax_calculation_rounding_method='round_globally', group_cash_rounding=True,
                       use_invoice_terms=True).action_nc_save()
        company = self.env.company
        self.assertEqual(company.account_sale_tax_id, self.sale_tax)
        self.assertEqual(company.account_purchase_tax_id, self.purchase_tax)
        self.assertTrue(company.qr_code)
        self.assertEqual(company.tax_calculation_rounding_method, 'round_globally')
        self.assertIn(self.env.ref('account.group_cash_rounding'), self.env.ref('base.group_user').implied_ids)
        self.assertEqual(self.env['ir.config_parameter'].sudo().get_param('account.use_invoice_terms'), 'True')
        again = self._settings()            # a fresh form reads the saved values back
        self.assertEqual(again.sale_tax_id, self.sale_tax)
        self.assertTrue(again.qr_code and again.group_cash_rounding and again.use_invoice_terms)
        # and a feature can be switched off again
        self._settings(group_cash_rounding=False).action_nc_save()
        self.assertNotIn(self.env.ref('account.group_cash_rounding'), self.env.ref('base.group_user').implied_ids)

    def test_a_purchase_tax_is_never_the_default_sales_tax(self):
        with self.assertRaises(ValidationError):
            self._settings(sale_tax_id=self.purchase_tax.id).action_nc_save()

    def test_another_companys_tax_or_account_is_refused(self):
        other = self.setup_other_company()
        foreign_tax = self.env['account.tax'].create({
            'name': 'Foreign', 'amount': 5, 'type_tax_use': 'sale', 'company_id': other['company'].id})
        with self.assertRaises(ValidationError):
            self._settings(sale_tax_id=foreign_tax.id).action_nc_save()
        with self.assertRaises(ValidationError):
            self.env.company.with_user(self.owner).write({'income_account_id': other['default_account_revenue'].id})

    def test_a_client_sent_company_is_ignored(self):
        other = self.setup_other_company()['company']
        self.env.company.qr_code = False
        other.qr_code = False
        self._settings(company_id=other.id, qr_code=True).action_nc_save()
        self.assertTrue(self.env.company.qr_code)
        self.assertFalse(other.qr_code)

    def test_accounting_defaults_save_through_the_company_delegation(self):
        account = self.company_data['default_account_revenue']
        self.env.company.with_user(self.owner).write({'income_account_id': account.id})
        self.assertEqual(self.env.company.income_account_id, account)
        with self.assertRaises(AccessError):      # not in the list: the native access check refuses it
            self.env.company.with_user(self.owner).write({'currency_id': self.env.ref('base.EUR').id})

    def test_only_a_workspace_administrator_opens_business_settings(self):
        with self.assertRaises(AccessError):
            self._settings(user=self.clerk)
        with self.assertRaises(AccessError):
            self.env['res.config.settings'].with_user(self.owner).create({})

    def test_an_invalid_option_is_refused(self):
        # the transient accepts any string; the settings validation refuses it before anything is applied
        with self.assertRaises(ValidationError):
            self._settings(tax_calculation_rounding_method='not_a_method').action_nc_save()
