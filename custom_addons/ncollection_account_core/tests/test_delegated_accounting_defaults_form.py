# -*- coding: utf-8 -*-
"""Settings > Accounting Defaults opens editable in the browser for the Workspace Administrator and an Accounting
Defaults delegate - who still cannot change the company's tax configuration, on screen or on save - and read-only for
an accounting clerk; the Accounting app of the Settings page is not even sent to a Business Settings delegate."""
from lxml import etree

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import new_test_user, tagged

from odoo.addons.ncollection_account_core.models.res_company_settings import ACCOUNTING_CONTROL_FIELDS


@tagged('post_install', '-at_install')
class TestDelegatedAccountingDefaultsForm(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        company = cls.env.company
        common = {'company_id': company.id, 'company_ids': [(6, 0, company.ids)]}
        cls.owner = new_test_user(
            cls.env, login='dad_owner',
            groups='base.group_user,ncollection_core.group_role_owner,account.group_account_user', **common)
        cls.clerk = new_test_user(cls.env, login='dad_clerk', groups='base.group_user,account.group_account_invoice',
                                  **common)
        cls.defaults_delegate = new_test_user(
            cls.env, login='dad_defaults',
            groups='base.group_user,account.group_account_invoice,ncollection_core.group_settings_accounting_defaults',
            **common)
        cls.business_delegate = new_test_user(
            cls.env, login='dad_business',
            groups='base.group_user,account.group_account_invoice,ncollection_core.group_settings_business',
            **common)
        cls.form = cls.env.ref('ncollection_account_core.view_nc_accounting_defaults_form')
        config = cls.env['ncollection.workspace.config'].search([], limit=1)
        if config:
            config.sudo().settings_policy = False

    def _form(self, user):
        return etree.fromstring(self.env['res.company'].with_user(user).get_views(
            [(self.form.id, 'form')])['views']['form']['arch'])

    @staticmethod
    def _editable(node):
        return node.get('edit') not in ('False', 'false', '0')

    @staticmethod
    def _readonly(node, name):
        return node.xpath('//field[@name="%s"]' % name)[0].get('readonly') in ('True', 'true', '1')

    def test_the_owner_edits_every_default_on_the_form(self):
        node = self._form(self.owner)
        self.assertTrue(self._editable(node))
        for name in ('nc_default_receivable_id', 'income_account_id', 'account_sale_tax_id', 'transfer_account_id'):
            self.assertFalse(self._readonly(node, name), name)
        self.assertTrue(self._readonly(node, 'name'))

    def test_a_defaults_delegate_edits_the_accounts_but_never_the_taxes(self):
        node = self._form(self.defaults_delegate)
        self.assertTrue(self._editable(node))
        self.assertFalse(self._readonly(node, 'income_account_id'))
        for name in ACCOUNTING_CONTROL_FIELDS & {f.get('name') for f in node.iter('field')}:
            self.assertTrue(self._readonly(node, name), '%s must be read-only for a delegate' % name)
        income = self.company_data['default_account_revenue']
        self.env.company.with_user(self.defaults_delegate).write({'income_account_id': income.id})
        self.assertEqual(self.env.company.income_account_id, income)
        with self.assertRaises(AccessError):
            self.env.company.with_user(self.defaults_delegate).write(
                {'account_sale_tax_id': self.company_data['default_tax_sale'].id})

    def test_a_clerk_gets_a_read_only_form_and_cannot_write(self):
        self.assertFalse(self._editable(self._form(self.clerk)))
        with self.assertRaises(AccessError):
            self.env.company.with_user(self.clerk).write(
                {'income_account_id': self.company_data['default_account_revenue'].id})

    def test_the_accounting_app_is_not_sent_to_a_business_delegate(self):
        def apps(user):
            arch = self.env['ncollection.business.settings'].with_user(user).get_views(
                [(False, 'form')])['views']['form']['arch']
            return {app.get('name') for app in etree.fromstring(arch).iter('app')}
        self.assertIn('account', apps(self.owner))
        self.assertNotIn('account', apps(self.business_delegate))
        with self.assertRaises(AccessError):
            self.env.company.with_user(self.business_delegate).write({'quick_edit_mode': 'out_invoices'})

    def test_every_field_of_the_defaults_page_belongs_to_the_defaults_section(self):
        """The exchange, early-payment and accrual defaults used to fall back to 'business': a Business Settings
        delegate could change them, an Accounting Defaults delegate could not."""
        exchange = self.env.company.currency_exchange_journal_id
        node = self._form(self.business_delegate)
        self.assertFalse(self._editable(node), 'nothing on Accounting Defaults is a Business Settings field')
        with self.assertRaises(AccessError):
            self.env.company.with_user(self.business_delegate).write({'currency_exchange_journal_id': exchange.id})
        node = self._form(self.defaults_delegate)
        for name in ('currency_exchange_journal_id', 'revenue_accrual_account_id',
                     'automatic_entry_default_journal_id'):
            self.assertFalse(self._readonly(node, name), name)
        self.env.company.with_user(self.defaults_delegate).write({'currency_exchange_journal_id': exchange.id})

    def test_the_defaults_keep_odoos_journal_and_account_types(self):
        sales = self.company_data['default_journal_sale']
        receivable = self.company_data['default_account_receivable']
        company = self.env.company.with_user(self.owner)
        for vals in ({'currency_exchange_journal_id': sales.id}, {'automatic_entry_default_journal_id': sales.id},
                     {'income_currency_exchange_account_id': receivable.id},
                     {'expense_currency_exchange_account_id': receivable.id},
                     {'revenue_accrual_account_id': receivable.id}, {'expense_accrual_account_id': receivable.id}):
            with self.subTest(vals=list(vals)), self.assertRaises(ValidationError):
                company.write(vals)
        company.write({'automatic_entry_default_journal_id': self.company_data['default_journal_misc'].id})

    def test_a_default_set_on_the_form_drives_the_next_invoice(self):
        income = self.copy_account(self.company_data['default_account_revenue'])
        self.env.company.with_user(self.owner).write({'income_account_id': income.id})
        product = self.env['product.product'].create({'name': 'DAD service', 'type': 'service',
                                                      'categ_id': self.env['product.category'].create(
                                                          {'name': 'DAD empty category'}).id})
        product.categ_id.property_account_income_categ_id = False
        product.property_account_income_id = False
        move = self.init_invoice('out_invoice', products=product)
        self.assertEqual(move.invoice_line_ids.account_id, income)
