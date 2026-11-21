# -*- coding: utf-8 -*-
"""The tenant Owner manages the company's whole legitimate accounting configuration from Settings > Accounting
(Main Currency, Fiscal Country, Chart of Accounts, taxes, cash basis, multi-currency, credit limit, ...), stored where
Odoo stores it - while the accounting identity is refused once entries exist, a chart needing an uninstalled
localization is refused, a non-administrator is refused, another company is never touched, and the Owner still holds
neither base.group_system nor Odoo's General Settings."""
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.tests import new_test_user, tagged

from odoo.addons.ncollection_account_core.wizards.business_settings import (
    IDENTITY_SETTINGS, NATIVE_ACCOUNTING_SETTINGS)


@tagged('post_install', '-at_install')
class TestAccountingSettingsComplete(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        company = cls.env.company
        common = {'company_id': company.id, 'company_ids': [(6, 0, company.ids)]}
        cls.owner = new_test_user(
            cls.env, login='acs_owner',
            groups='base.group_user,ncollection_core.group_role_owner,account.group_account_user', **common)
        cls.clerk = new_test_user(cls.env, login='acs_clerk', groups='base.group_user,account.group_account_user',
                                  **common)

    def _settings(self, user=None, **vals):
        return self.env['ncollection.business.settings'].with_user(user or self.owner).create(vals)

    def _no_entries(self):
        self.env['account.move.line'].sudo().search([('company_id', 'child_of', self.env.company.root_id.id)]) \
            .move_id.button_draft()
        self.env['account.move'].sudo().search(
            [('company_id', 'child_of', self.env.company.root_id.id)]).with_context(force_delete=True).unlink()
        self.assertFalse(self.env.company.root_id._existing_accounting())

    def _post_an_invoice(self):
        self.init_invoice('out_invoice', amounts=[100.0], post=True)
        self.assertTrue(self.env.company.root_id._existing_accounting())

    # ------------------------------------------------------------------ the regression: nothing disappears
    def test_every_accounting_setting_is_on_the_owners_settings_page(self):
        arch = self.env['ncollection.business.settings'].with_user(self.owner).get_views(
            [(False, 'form')])['views']['form']['arch']
        for name in NATIVE_ACCOUNTING_SETTINGS + tuple(IDENTITY_SETTINGS) + ('account_default_credit_limit',):
            self.assertIn('name="%s"' % name, arch, '%s is missing from the Settings page' % name)
            self.assertIn(name, self.env['ncollection.business.settings']._nc_settings_spec())
            self.assertIn(name, self.env['res.config.settings']._fields, '%s is not a native setting' % name)
        values = self._settings()
        self.assertEqual(values.currency_id, self.env.company.currency_id)
        self.assertEqual(values.account_fiscal_country_id, self.env.company.account_fiscal_country_id)
        self.assertEqual(values.chart_template, self.env.company.chart_template)

    def test_the_owner_changes_the_wider_accounting_settings_and_they_persist(self):
        journal = self.company_data['default_journal_misc']
        base = self.company_data['default_account_revenue']
        self._settings(group_multi_currency=True, tax_exigibility=True, tax_cash_basis_journal_id=journal.id,
                       account_cash_basis_base_account_id=base.id, account_use_credit_limit=True,
                       account_default_credit_limit=5000.0, autopost_bills=True, group_analytic_accounting=True,
                       quick_edit_mode='out_and_in_invoices').action_nc_save()
        company = self.env.company
        self.assertTrue(company.tax_exigibility and company.account_use_credit_limit and company.autopost_bills)
        self.assertEqual(company.tax_cash_basis_journal_id, journal)
        self.assertEqual(company.quick_edit_mode, 'out_and_in_invoices')
        self.assertIn(self.env.ref('base.group_multi_currency'), self.env.ref('base.group_user').implied_ids)
        again = self._settings()
        self.assertEqual(again.account_default_credit_limit, 5000.0)
        self.assertTrue(again.group_multi_currency and again.group_analytic_accounting)

    # ------------------------------------------------------------------ the accounting identity
    def test_currency_and_fiscal_country_change_while_there_are_no_entries(self):
        self._no_entries()
        other_currency = self.env.ref('base.EUR') if self.env.company.currency_id != self.env.ref('base.EUR') \
            else self.env.ref('base.USD')
        country = self.env.ref('base.be')
        self._settings(currency_id=other_currency.id, account_fiscal_country_id=country.id).action_nc_save()
        self.assertEqual(self.env.company.currency_id, other_currency)
        self.assertEqual(self.env.company.account_fiscal_country_id, country)

    def test_the_identity_is_refused_once_entries_exist_and_nothing_changes(self):
        self._post_an_invoice()
        company = self.env.company
        before = (company.currency_id, company.account_fiscal_country_id, company.chart_template)
        page = self._settings()
        self.assertTrue(page.nc_has_accounting_entries)
        other_currency = self.env['res.currency'].with_context(active_test=False).search(
            [('id', '!=', company.currency_id.id)], limit=1)
        for vals in ({'currency_id': other_currency.id}, {'account_fiscal_country_id': self.env.ref('base.be').id}):
            with self.assertRaises(UserError):
                self._settings(**vals).action_nc_save()
        self.assertEqual((company.currency_id, company.account_fiscal_country_id, company.chart_template), before)

    def test_a_chart_needing_an_uninstalled_localization_is_refused(self):
        self._no_entries()
        Chart = self.env['account.chart.template']
        candidates = [code for code, tpl in Chart._get_chart_template_mapping().items()
                      if not tpl.get('installed')]
        self.assertTrue(candidates, 'expected at least one chart whose localization module is not installed')
        self.env.company.sudo().chart_template = False      # a company with no chart yet
        with self.assertRaisesRegex(UserError, 'localization package'):
            self._settings()._nc_apply_chart_template(self.env.company, candidates[0])
        self.assertFalse(self.env.company.chart_template)
        self.assertFalse(self.env['ir.module.module'].search(
            [('state', 'in', ('to install', 'to upgrade'))]), 'a setting must never schedule a module install')

    def test_an_existing_chart_is_never_replaced_from_settings(self):
        # Odoo's loader would delete the company's accounts, taxes and journals: refused, nothing deleted
        self._no_entries()
        company = self.env.company
        installed = [code for code, tpl in self.env['account.chart.template']._get_chart_template_mapping().items()
                     if tpl.get('installed') and code != company.chart_template]

        def counts():
            return (self.env['account.account'].search_count([('company_ids', 'in', company.ids)]),
                    self.env['account.tax'].search_count([('company_id', '=', company.id)]),
                    self.env['account.journal'].search_count([('company_id', '=', company.id)]))
        before = counts()
        with self.assertRaisesRegex(UserError, 'already has a chart'):
            self._settings()._nc_apply_chart_template(company, installed[0] if installed else 'generic_coa')
        self.assertEqual(counts(), before)

    def test_odoo_view_only_rules_are_enforced_at_the_orm(self):
        company = self.env.company
        bank = self.company_data['default_journal_bank']
        receivable = self.company_data['default_account_receivable']
        for vals in ({'tax_cash_basis_journal_id': bank.id}, {'account_discount_income_allocation_id': receivable.id},
                     {'account_storno': True}):
            with self.assertRaises(ValidationError):
                company.with_user(self.owner).write(vals)
        if not company.display_account_storno:
            with self.assertRaisesRegex(UserError, 'not available'):
                self._settings(account_storno=True).action_nc_save()
        self.assertFalse(company.account_storno)

    def test_a_branch_keeps_its_parents_currency_and_cash_basis(self):
        self._no_entries()
        branch = self.env['res.company'].create({'name': 'ACS Branch', 'parent_id': self.env.company.id})
        self.owner.sudo().company_ids |= branch
        settings = self._settings().with_company(branch)
        with self.assertRaisesRegex(UserError, 'parent'):
            settings._nc_apply_currency(branch, self.env.ref('base.EUR').id)
        with self.assertRaises(ValidationError):
            branch.with_user(self.owner).with_company(branch).write({'tax_exigibility': True})

    # ------------------------------------------------------------------ permissions and isolation
    def test_a_non_administrator_is_refused(self):
        with self.assertRaises(AccessError):
            self._settings(user=self.clerk, group_multi_currency=True).action_nc_save()
        with self.assertRaises(AccessError):
            self._settings()._nc_identity_guard.__func__(
                self.env['ncollection.business.settings'].with_user(self.clerk), self.env.company, 'currency_id')

    def test_the_owner_is_still_not_a_system_administrator(self):
        self.assertFalse(self.owner.has_group('base.group_system'))
        self.assertFalse(self.owner.has_group('base.group_erp_manager'))
        self.assertFalse(self.env['res.config.settings'].with_user(self.owner).has_access('create'))
        with self.assertRaises(AccessError):
            self.env.company.with_user(self.owner).write({'currency_id': self.env.ref('base.EUR').id})

    def test_another_company_is_never_touched(self):
        other = self.setup_other_company()['company']
        before = (other.currency_id, other.tax_exigibility, other.autopost_bills)
        self._settings(tax_exigibility=True, autopost_bills=True).action_nc_save()
        self.assertEqual((other.currency_id, other.tax_exigibility, other.autopost_bills), before)
