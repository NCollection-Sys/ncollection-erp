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
    def _generic_countries(self):
        """Countries whose own chart IS the generic chart (no localization): consistent with generic_coa."""
        Chart = self.env['account.chart.template']
        return self.env['res.country'].search([('code', 'in', ('SY', 'AQ', 'BV', 'HM'))]).filtered(
            lambda c: Chart._guess_chart_template(c) == 'generic_coa')

    def _structures(self):
        company = self.env.company
        return (company.chart_template, company.currency_id, company.account_fiscal_country_id,
                self.env['account.account'].search_count([('company_ids', 'in', company.ids)]),
                self.env['account.tax'].search_count([('company_id', '=', company.id)]),
                self.env['account.journal'].search_count([('company_id', '=', company.id)]),
                self.env['account.fiscal.position'].search_count([('company_id', '=', company.id)]))

    def test_currency_and_fiscal_country_change_when_they_stay_consistent_with_the_chart(self):
        self._no_entries()
        countries = self._generic_countries()
        self.assertGreaterEqual(len(countries), 2, 'expected two countries served by the generic chart')
        company = self.env.company
        company.sudo().write({'chart_template': 'generic_coa', 'account_fiscal_country_id': countries[0].id})
        other_currency = self.env.ref('base.EUR') if company.currency_id != self.env.ref('base.EUR') \
            else self.env.ref('base.USD')
        self._settings(currency_id=other_currency.id, account_fiscal_country_id=countries[1].id).action_nc_save()
        self.assertEqual(company.currency_id, other_currency)
        self.assertEqual(company.account_fiscal_country_id, countries[1])

    # ------------------------------------------------------------------ an Egyptian company on the generic US chart
    def _egypt_on_the_generic_chart(self):
        """The production defect: fiscal country Egypt, books in EGP, but the generic chart and its US taxes."""
        self._no_entries()
        company = self.env.company
        company.sudo().write({'chart_template': 'generic_coa', 'country_id': self.env.ref('base.eg').id,
                              'account_fiscal_country_id': self.env.ref('base.eg').id})
        return company

    def test_an_egyptian_company_on_the_generic_chart_is_flagged_and_frozen(self):
        company = self._egypt_on_the_generic_chart()
        self.assertEqual(self.env['account.chart.template']._guess_chart_template(self.env.ref('base.eg')), 'eg')
        page = self._settings()
        self.assertTrue(page.nc_localization_mismatch)
        arch = page.get_views([(False, 'form')])['views']['form']['arch']
        self.assertIn('controlled localization migration', arch)
        before = self._structures()
        egp = self.env.ref('base.EGP')
        attempts = (
            {'currency_id': egp.id if company.currency_id != egp else self.env.ref('base.USD').id},
            {'account_fiscal_country_id': self.env.ref('base.us').id},     # "fix" the label back to the US
            {'account_fiscal_country_id': self.env.ref('base.ae').id},
            {'chart_template': 'eg'},
        )
        for vals in attempts:
            with self.assertRaises(UserError, msg='%s must be refused' % vals) as caught:
                self._settings(**vals).action_nc_save()
            self.assertTrue('localization' in str(caught.exception) or 'chart' in str(caught.exception),
                            'the refusal must explain why: %s' % caught.exception)
        self.assertEqual(self._structures(), before, 'nothing may change, nothing may be deleted')
        self.assertFalse(self.env['ir.module.module'].search([('state', 'in', ('to install', 'to upgrade'))]))

    def test_a_fiscal_country_that_does_not_match_the_chart_is_refused(self):
        # a correctly localized generic company cannot be relabelled Egyptian while keeping generic accounts/taxes
        self._no_entries()
        company = self.env.company
        company.sudo().write({'chart_template': 'generic_coa',
                              'account_fiscal_country_id': self._generic_countries()[0].id})
        before = self._structures()
        with self.assertRaisesRegex(UserError, 'controlled localization migration'):
            self._settings(account_fiscal_country_id=self.env.ref('base.eg').id).action_nc_save()
        self.assertEqual(self._structures(), before)

    def test_a_country_chart_keeps_its_countrys_currency(self):
        self._no_entries()
        company = self.env.company
        # the rule reads only the company's chart code and fiscal country: no localization module is needed
        code, country = 'ae', self.env.ref('base.ae')
        self.assertTrue(country.currency_id)
        company.sudo().write({'chart_template': code, 'account_fiscal_country_id': country.id,
                              'currency_id': country.currency_id.id})
        eur = self.env.ref('base.EUR')
        other = eur if country.currency_id != eur else self.env.ref('base.USD')
        with self.assertRaisesRegex(UserError, 'keeps its books in'):
            self._settings(currency_id=other.id).action_nc_save()
        self.assertEqual(company.currency_id, country.currency_id)

    def test_a_journal_already_kept_in_the_new_currency_blocks_the_change(self):
        self._no_entries()
        company = self.env.company
        company.sudo().write({'chart_template': 'generic_coa',
                              'account_fiscal_country_id': self._generic_countries()[0].id})
        eur = self.env.ref('base.EUR') if company.currency_id != self.env.ref('base.EUR') else self.env.ref('base.USD')
        eur.active = True
        self.company_data['default_journal_bank'].sudo().currency_id = eur
        with self.assertRaisesRegex(UserError, 'foreign currency'):
            self._settings(currency_id=eur.id).action_nc_save()
        self.assertNotEqual(company.currency_id, eur)

    # ------------------------------------------------------------------ delegates and accounting defaults
    def test_a_business_settings_delegate_cannot_touch_accounting_controls(self):
        delegate = new_test_user(
            self.env, login='acs_delegate', groups='base.group_user,ncollection_core.group_settings_business',
            company_id=self.env.company.id, company_ids=[(6, 0, self.env.company.ids)])
        company = self.env.company
        multi = self.env.ref('base.group_multi_currency').sudo()
        users = self.env.ref('base.group_user').sudo()
        # The invariant must hold whatever the database's currency configuration already is: a save that changes
        # nothing is never an error, so every refused write below is the OPPOSITE of the current state.
        eur = self.env.ref('base.EUR')
        other_currency = eur if company.currency_id != eur else self.env.ref('base.USD')
        other_currency.sudo().active = True            # a real, selectable currency (activating may imply the group)
        for multi_currency_on in (False, True):
            if multi_currency_on:
                users._apply_group(multi)
            else:
                users._remove_group(multi)
            current = self._settings()._nc_native_values(['group_multi_currency', 'autopost_bills'])
            self.assertEqual(current['group_multi_currency'], multi_currency_on)
            before = (company.autopost_bills, company.account_sale_tax_id, company.currency_id)
            for vals in ({'autopost_bills': not current['autopost_bills']},
                         {'group_multi_currency': not current['group_multi_currency']},
                         {'currency_id': other_currency.id}):
                with self.assertRaises(AccessError, msg='delegate wrote %s' % vals):
                    self._settings(user=delegate, **vals).action_nc_save()
            with self.assertRaises(AccessError):
                company.with_user(delegate).write({'autopost_bills': not before[0]})
            self.assertEqual((company.autopost_bills, company.account_sale_tax_id, company.currency_id), before)
            self.assertEqual(self._settings()._nc_native_values(['group_multi_currency'])['group_multi_currency'],
                             multi_currency_on, 'the refused save changed the multi-currency state')
            # an authorised save of the page as it stands is accepted and changes nothing
            self._settings(user=delegate).action_nc_save()
            self.assertEqual((company.autopost_bills, company.account_sale_tax_id, company.currency_id), before)
        # another company is never reachable, even to a user allowed into it
        other = self.env['res.company'].create({'name': 'ACS Other Co'})
        with self.assertRaises(AccessError):
            other.with_user(delegate).write({'autopost_bills': not other.autopost_bills})
        delegate.sudo().company_ids = [(4, other.id)]
        with self.assertRaises(AccessError):
            other.with_user(delegate).with_company(other).write({'autopost_bills': not other.autopost_bills})

    def test_accounting_defaults_set_by_the_owner_drive_the_next_invoice(self):
        company = self.env.company
        revenue = self.env['account.account'].create({
            'name': 'ACS Revenue', 'code': 'ACS400', 'account_type': 'income', 'company_ids': [(6, 0, company.ids)]})
        receivable = self.env['account.account'].create({
            'name': 'ACS Receivable', 'code': 'ACS120', 'account_type': 'asset_receivable', 'reconcile': True,
            'company_ids': [(6, 0, company.ids)]})
        tax = self.env['account.tax'].create({'name': 'ACS 9', 'amount': 9, 'type_tax_use': 'sale'})
        owner_company = company.with_user(self.owner)
        owner_company.write({'income_account_id': revenue.id, 'nc_default_receivable_id': receivable.id})
        self._settings(sale_tax_id=tax.id).action_nc_save()
        partner = self.env['res.partner'].create({'name': 'ACS New Customer'})
        product = self.env['product.product'].create({'name': 'ACS Service', 'type': 'service', 'lst_price': 100})
        product.product_tmpl_id.categ_id.sudo().property_account_income_categ_id = False
        product.taxes_id = False
        invoice = self.env['account.move'].with_user(self.owner).create({
            'move_type': 'out_invoice', 'partner_id': partner.id,
            'invoice_line_ids': [(0, 0, {'product_id': product.id, 'quantity': 1, 'price_unit': 100})]})
        line = invoice.invoice_line_ids
        self.assertEqual(partner.with_company(company).property_account_receivable_id, receivable)
        self.assertEqual(invoice.line_ids.filtered(lambda x: x.display_type == 'payment_term').account_id, receivable)
        self.assertEqual(line.account_id, revenue)
        self.assertEqual(company.account_sale_tax_id, tax)
        self.assertEqual(self.env['product.product'].create({'name': 'ACS Next'}).taxes_id, tax,
                         'a new product takes the default sales tax')

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
        for vals in ({'tax_cash_basis_journal_id': bank.id}, {'account_discount_income_allocation_id': receivable.id}):
            with self.assertRaises(ValidationError):
                company.with_user(self.owner).write(vals)
        with self.assertRaises(AccessError):   # storno only through the Settings page's own handler
            company.with_user(self.owner).write({'account_storno': True})
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
        delegate = new_test_user(
            self.env, login='acs_delegate2', groups='base.group_user,ncollection_core.group_settings_business',
            company_id=self.env.company.id, company_ids=[(6, 0, self.env.company.ids)])
        before = self._structures()
        for user, vals in ((self.clerk, {'group_multi_currency': True}),
                           (delegate, {'chart_template': 'eg'}),
                           (delegate, {'account_fiscal_country_id': self.env.ref('base.eg').id})):
            with self.assertRaises(AccessError):
                self._settings(user=user, **vals).action_nc_save()
        self.assertEqual(self._structures(), before)
        page = self._settings(user=delegate)
        self.assertFalse(page.nc_is_tenant_admin, 'the Accounting app is hidden from a section delegate')

    # ------------------------------------------------------------------ loading a chart: only an empty tree
    def _empty_company(self, country):
        company = self.env['res.company'].create({'name': 'ACS Empty', 'country_id': country.id,
                                                  'account_fiscal_country_id': country.id})
        self.owner.sudo().company_ids |= company
        self.assertFalse(company.chart_template)
        return company

    def test_a_chart_loads_on_an_empty_company_of_its_country(self):
        country = self._generic_countries()[0]
        company = self._empty_company(country)
        settings = self._settings().with_company(company)
        settings._nc_apply_chart_template(company, 'generic_coa')
        self.assertEqual(company.chart_template, 'generic_coa')
        self.assertTrue(self.env['account.account'].search_count([('company_ids', 'in', company.ids)]))
        self.assertFalse(self.env['ir.module.module'].search([('state', 'in', ('to install', 'to upgrade'))]))

    def test_a_company_without_a_chart_but_with_accounts_is_never_wiped(self):
        country = self._generic_countries()[0]
        company = self._empty_company(country)
        account = self.env['account.account'].create({'name': 'ACS Hand-made', 'code': 'ACS999',
                                                      'account_type': 'income', 'company_ids': [(6, 0, company.ids)]})
        with self.assertRaisesRegex(UserError, 'would delete them'):
            self._settings().with_company(company)._nc_apply_chart_template(company, 'generic_coa')
        self.assertTrue(account.exists())

    def test_a_grandchild_the_user_does_not_belong_to_blocks_the_load(self):
        country = self._generic_countries()[0]
        company = self._empty_company(country)
        child = self.env['res.company'].create({'name': 'ACS Child', 'parent_id': company.id})
        self.env['res.company'].create({'name': 'ACS Grandchild', 'parent_id': child.id})
        self.owner.sudo().company_ids |= child                      # a member of the child, not the grandchild
        with self.assertRaises(AccessError):
            self._settings().with_company(company)._nc_apply_chart_template(company, 'generic_coa')

    def test_every_chart_of_a_country_is_compatible(self):
        Page = self.env['ncollection.business.settings']
        mapping = self.env['account.chart.template']._get_chart_template_mapping()
        by_country = {}
        for code, template in mapping.items():
            if template.get('country_id'):
                by_country.setdefault(template['country_id'], set()).add(code)
        several = {cid: codes for cid, codes in by_country.items() if len(codes) > 1}
        self.assertTrue(several, 'expected at least one country with several charts (e.g. Germany)')
        cid, codes = next(iter(several.items()))
        self.assertEqual(Page._nc_compatible_charts(self.env['res.country'].browse(cid)), codes)
        self.assertEqual(Page._nc_compatible_charts(self._generic_countries()[0]), {'generic_coa'})

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
