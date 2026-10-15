# -*- coding: utf-8 -*-
"""Settings > Accounting Defaults: native company fields + two ir.default properties, per company, rights unchanged."""
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import AccessError
from odoo.tests import tagged


@tagged('post_install', '-at_install')
class TestAccountingDefaults(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company_data_2 = cls.setup_other_company()
        cls.company = cls.company_data['company']
        cls.company_b = cls.company_data_2['company']
        Account = cls.env['account.account']
        cls.recv_new = Account.create({'code': 'NCR100', 'name': 'New Receivable', 'account_type': 'asset_receivable',
                                       'reconcile': True})
        cls.pay_new = Account.create({'code': 'NCP100', 'name': 'New Payable', 'account_type': 'liability_payable',
                                      'reconcile': True})
        cls.inc_new = Account.create({'code': 'NCI100', 'name': 'New Income', 'account_type': 'income'})
        cls.exp_new = Account.create({'code': 'NCE100', 'name': 'New Expense', 'account_type': 'expense'})
        cls.recv_explicit = Account.create({'code': 'NCR200', 'name': 'Explicit Receivable',
                                            'account_type': 'asset_receivable', 'reconcile': True})
        cls.owner = cls._user('nc_def_owner', 'ncollection_core.group_role_owner')
        cls.accountant = cls._user('nc_def_acct', 'ncollection_core.group_role_accountant')

    @classmethod
    def _user(cls, login, role):
        return cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': login, 'login': login, 'company_id': cls.company.id, 'company_ids': [(6, 0, cls.company.ids)],
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id, cls.env.ref(role).id])]})

    def _defaults(self, company):
        defaults = self.env['ir.default'].sudo()
        return {model_field: defaults._get(*model_field, company_id=company.id)
                for model_field in (('res.partner', 'property_account_receivable_id'),
                                    ('res.partner', 'property_account_payable_id'))}

    # ------------------------------------------------------------------ what it exposes
    def test_the_screen_is_native_company_fields_only(self):
        company_fields = self.env['res.company']._fields
        form = self.env.ref('ncollection_account_core.view_nc_accounting_defaults_form')
        arch = form.arch
        for name in ('income_account_id', 'expense_account_id', 'transfer_account_id', 'revenue_accrual_account_id',
                     'expense_accrual_account_id', 'default_cash_difference_income_account_id',
                     'account_sale_tax_id', 'account_purchase_tax_id', 'currency_exchange_journal_id'):
            self.assertIn(name, company_fields)
            self.assertIn('name="%s"' % name, arch)

    def test_deferred_revenue_and_expense_are_not_pretended(self):
        """Community has no deferral engine; the screen offers the accrual accounts and says so."""
        self.assertNotIn('deferred_revenue_account_id', self.env['res.company']._fields)
        self.assertIn('Change Period', self.env.ref('ncollection_account_core.view_nc_accounting_defaults_form').arch)

    def test_the_action_opens_the_active_companys_form(self):
        action = self.env['res.company'].with_company(self.company_b).nc_action_accounting_defaults()
        self.assertEqual((action['res_model'], action['res_id']), ('res.company', self.company_b.id))
        self.assertEqual(action['view_mode'], 'form')

    def test_the_menu_is_for_system_administrators_only(self):
        menu = self.env.ref('ncollection_account_core.menu_nc_accounting_defaults')
        self.assertEqual(menu.group_ids, self.env.ref('base.group_system'))
        self.assertEqual(menu.parent_id, self.env.ref('base.menu_administration'))

    # ------------------------------------------------------------------ the two properties
    def test_the_owner_sets_the_defaults_in_this_company_only(self):
        before_b = self._defaults(self.company_b)
        self.company.with_user(self.owner).write({
            'nc_default_receivable_id': self.recv_new.id, 'nc_default_payable_id': self.pay_new.id})
        mine = self._defaults(self.company)
        self.assertEqual(mine[('res.partner', 'property_account_receivable_id')], self.recv_new.id)
        self.assertEqual(mine[('res.partner', 'property_account_payable_id')], self.pay_new.id)
        self.assertEqual(self._defaults(self.company_b), before_b)             # the other company is untouched
        # and they read back through the form's own fields
        self.assertEqual(self.company.nc_default_receivable_id, self.recv_new)
        before_b_receivable = before_b[('res.partner', 'property_account_receivable_id')] or False
        self.assertEqual(self.company_b.nc_default_receivable_id.id, before_b_receivable)

    def test_a_customer_without_its_own_account_follows_the_default_and_one_with_its_own_keeps_it(self):
        """A company-dependent property's default is a live fallback (Odoo's own semantics): it applies to every
        record with no value of its own. A customer with an explicit account is never touched."""
        explicit = self.env['res.partner'].with_company(self.company).create({
            'name': 'Explicit Customer', 'property_account_receivable_id': self.recv_explicit.id})
        self.company.with_user(self.owner).write({'nc_default_receivable_id': self.recv_new.id})
        fresh = self.env['res.partner'].with_company(self.company).create({'name': 'New Customer'})
        self.assertEqual(fresh.with_company(self.company).property_account_receivable_id, self.recv_new)
        self.assertEqual(explicit.with_company(self.company).property_account_receivable_id, self.recv_explicit)

    def test_a_wrong_kind_of_account_or_another_companys_account_is_refused(self):
        from odoo.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            # an expense account
            self.company.with_user(self.owner).write({'nc_default_receivable_id': self.exp_new.id})
        with self.assertRaises(ValidationError):
            self.company.with_user(self.owner).write({'nc_default_payable_id': self.recv_new.id})     # a receivable
        foreign = self.env['account.account'].create({
            'code': 'NCF100', 'name': 'Foreign Receivable', 'account_type': 'asset_receivable', 'reconcile': True,
            'company_ids': [(6, 0, self.company_b.ids)]})
        from odoo.exceptions import AccessError
        refused = False
        try:                                                  # the record rule or the ORM check, whichever first
            self.company.with_user(self.owner).write({'nc_default_receivable_id': foreign.id})
        except (ValidationError, AccessError):
            refused = True
        self.assertTrue(refused)
        stored = self._defaults(self.company)[('res.partner', 'property_account_receivable_id')]
        self.assertNotEqual(stored, foreign.id)

    def test_clearing_a_default_removes_it(self):
        self.company.with_user(self.owner).write({'nc_default_payable_id': self.pay_new.id})
        self.company.with_user(self.owner).write({'nc_default_payable_id': False})
        self.assertFalse(self._defaults(self.company)[('res.partner', 'property_account_payable_id')])

    def test_native_company_accounts_are_written_through(self):
        self.company.with_user(self.owner).write({
            'income_account_id': self.inc_new.id, 'expense_account_id': self.exp_new.id,
            'revenue_accrual_account_id': self.inc_new.id})
        self.assertEqual(self.company.income_account_id, self.inc_new)
        self.assertEqual(self.company.revenue_accrual_account_id, self.inc_new)

    # ------------------------------------------------------------------ who may
    def test_only_system_administrators_can_change_them_and_the_orm_says_so(self):
        for values in ({'nc_default_receivable_id': self.recv_new.id}, {'income_account_id': self.inc_new.id}):
            with self.assertRaises(AccessError):
                self.company.with_user(self.accountant).write(values)
        stored = self._defaults(self.company)[('res.partner', 'property_account_receivable_id')]
        self.assertNotEqual(stored, self.recv_new.id)
