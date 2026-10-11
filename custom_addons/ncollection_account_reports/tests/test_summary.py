# -*- coding: utf-8 -*-
"""The Summary's financial statements are the report engine's rows, scoped to the company and the role."""
from datetime import date
from unittest.mock import patch

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.tests import tagged

DASH = 'ncollection.dashboard.data'
BS = 'ncollection.account.report.balance.sheet'
PL = 'ncollection.account.report.profit.loss'
PL_TYPES = ('income', 'income_other', 'expense', 'expense_other', 'expense_direct_cost', 'expense_depreciation')
ASSET_TYPES = ('asset_receivable', 'asset_cash', 'asset_current', 'asset_prepayments',
               'asset_non_current', 'asset_fixed')


@tagged('post_install', '-at_install')
class TestSummaryStatements(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company_data_2 = cls.setup_other_company()
        cls.today = date.today()
        cls.accountant = cls._user('nc_sum_acct', [
            'ncollection_core.group_role_accountant', 'account.group_account_readonly'])
        cls.employee = cls._user('nc_sum_emp', ['ncollection_core.group_role_employee'])

    @classmethod
    def _user(cls, login, groups, company=None):
        company = company or cls.env.company
        return cls.env['res.users'].create({
            'name': login, 'login': login, 'company_id': company.id, 'company_ids': [(6, 0, company.ids)],
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id] + [cls.env.ref(g).id for g in groups])]})

    @classmethod
    def _post(cls, company_data, debit_account, credit_account, amount, company=None):
        company = company or company_data['company']
        move = cls.env['account.move'].with_company(company).create({
            'move_type': 'entry', 'journal_id': company_data['default_journal_misc'].id,
            'date': cls.today, 'line_ids': [
                (0, 0, {'account_id': debit_account.id, 'debit': amount, 'credit': 0.0}),
                (0, 0, {'account_id': credit_account.id, 'debit': 0.0, 'credit': amount})]})
        move.action_post()

    def _widgets(self, user=None):
        service = self.env[DASH].with_user(user) if user else self.env[DASH]
        payload = service.get_dashboard_payload()
        return payload, {w['key']: w for w in payload['widgets']}

    # --------------------------------------------------------------------- the rows ARE the engine's
    def test_statement_rows_are_exactly_the_engine_rows(self):
        self._post(self.company_data, self.company_data['default_account_receivable'],
                   self.company_data['default_account_revenue'], 1234.5)
        _payload, widgets = self._widgets(self.accountant)
        for key, model in (('balance_sheet_summary', BS), ('profit_loss_summary', PL)):
            widget = widgets[key]
            wizard = self.env[model].with_user(self.accountant).create({
                'date_from': self.today.replace(month=1, day=1), 'date_to': self.today,
                'target_move': 'posted', 'comparison_type': 'none'})
            payload = wizard._nc_export_payload()
            index = next(i for i, c in enumerate(payload['columns']) if c['numeric'])
            expected = [(r['cells'][0]['text'], r['cells'][index]['value'], r['level'])
                        for r in payload['rows'] if r['level'] <= 1]
            self.assertEqual([(r['label'], r['value'], r['level']) for r in widget['rows']], expected, key)
            self.assertEqual(widget['type'], 'statement')

    def test_profit_and_balance_reconcile_with_the_ledger_not_just_the_engine(self):
        """An independent read of the journal items: the Summary must agree with the books."""
        self._post(self.company_data, self.company_data['default_account_receivable'],
                   self.company_data['default_account_revenue'], 1000.0)
        self._post(self.company_data, self.company_data['default_account_expense'],
                   self.company_data['default_account_payable'], 300.0)
        _payload, widgets = self._widgets(self.accountant)
        profit = next(r for r in widgets['profit_loss_summary']['rows'] if r['label'] == 'NET PROFIT')
        lines = self.env['account.move.line'].search([
            ('parent_state', '=', 'posted'), ('company_id', '=', self.env.company.id),
            ('date', '>=', self.today.replace(month=1, day=1)), ('date', '<=', self.today),
            ('account_id.account_type', 'in', PL_TYPES)])
        self.assertAlmostEqual(profit['value'], -sum(lines.mapped('balance')), places=2)
        assets = next(r for r in widgets['balance_sheet_summary']['rows'] if r['label'] == 'TOTAL ASSETS')
        asset_lines = self.env['account.move.line'].search([
            ('parent_state', '=', 'posted'), ('company_id', '=', self.env.company.id), ('date', '<=', self.today),
            ('account_id.account_type', 'in', ASSET_TYPES)])
        self.assertAlmostEqual(assets['value'], sum(asset_lines.mapped('balance')), places=2)

    def test_one_engine_pass_per_statement(self):
        calls = []
        original = type(self.env[BS])._nc_export_payload

        def counting(wizard, *args, **kwargs):
            calls.append(wizard._name)
            return original(wizard, *args, **kwargs)

        with patch.object(type(self.env[BS]), '_nc_export_payload', counting), \
                patch.object(type(self.env[PL]), '_nc_export_payload', counting):
            self._widgets(self.accountant)
        self.assertEqual(sorted(calls), sorted([BS, PL]))

    # --------------------------------------------------------------------- states
    def test_a_company_without_postings_says_so_instead_of_printing_zeros(self):
        company_b = self.company_data_2['company']
        user = self._user('nc_sum_b', ['ncollection_core.group_role_accountant', 'account.group_account_readonly'],
                          company=company_b)
        payload = self.env[DASH].with_user(user).with_company(company_b).get_dashboard_payload()
        widgets = {w['key']: w for w in payload['widgets']}
        self.assertTrue(widgets['balance_sheet_summary']['empty'])
        self.assertTrue(widgets['profit_loss_summary']['empty'])

    def test_the_drill_down_opens_the_existing_report(self):
        _payload, widgets = self._widgets(self.accountant)
        bs_action = self.env.ref('ncollection_account_reports.action_nc_view_balance_sheet')
        self.assertEqual(widgets['balance_sheet_summary']['action_id'], bs_action.id)

    # --------------------------------------------------------------------- scope and permission
    def test_the_summary_shows_the_active_company_only(self):
        self._post(self.company_data, self.company_data['default_account_receivable'],
                   self.company_data['default_account_revenue'], 111.0)
        self._post(self.company_data_2, self.company_data_2['default_account_receivable'],
                   self.company_data_2['default_account_revenue'], 777.0)
        _payload, widgets = self._widgets(self.accountant)
        blob = str(widgets['balance_sheet_summary']) + str(widgets['profit_loss_summary'])
        self.assertIn('111.0', blob)
        self.assertNotIn('777.0', blob)
        company_b = self.company_data_2['company']
        user_b = self._user('nc_sum_b2', ['ncollection_core.group_role_accountant',
                                          'account.group_account_readonly'], company=company_b)
        payload_b = self.env[DASH].with_user(user_b).with_company(company_b).get_dashboard_payload()
        blob_b = str({w['key']: w for w in payload_b['widgets']}['profit_loss_summary'])
        self.assertIn('777.0', blob_b)
        self.assertNotIn('111.0', blob_b)
        self.assertEqual(payload_b['meta']['company_name'], company_b.name)

    def test_a_non_financial_role_gets_no_statement(self):
        _payload, widgets = self._widgets(self.employee)
        self.assertNotIn('balance_sheet_summary', widgets)
        self.assertNotIn('profit_loss_summary', widgets)

    def test_the_landing_action_and_menu_are_called_summary(self):
        self.assertEqual(self.env.ref('ncollection_core.action_ncollection_dashboard').name, 'Summary')
        self.assertEqual(self.env.ref('ncollection_core.menu_ncollection_dashboard_root').name, 'Summary')

    def test_arabic_labels(self):
        self.env['res.lang']._activate_lang('ar_001')
        payload = self.env[DASH].with_user(self.accountant).with_context(lang='ar_001').get_dashboard_payload()
        labels = {w['key']: w['label'] for w in payload['widgets']}
        self.assertEqual(labels['balance_sheet_summary'], 'الميزانية العمومية')
        self.assertEqual(labels['profit_loss_summary'], 'الأرباح والخسائر')
