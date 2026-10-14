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
            wizard.exclude_zero = True               # the Summary leaves zero-balance accounts out, nothing else
            expected = [(r['label'], r['current_amount'], r['level'], r['key'] or '')
                        for r in wizard._nc_compute_lines()]
            self.assertEqual([(r['label'], r['value'], r['level'], r['key']) for r in widget['rows']],
                             expected, key)
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
        """Each engine runs exactly once per Summary load; Expenses and Net Profit are read off the Profit & Loss
        pass already made, so they cost no engine run of their own."""
        calls = []
        originals = {BS: type(self.env[BS])._nc_compute_lines, PL: type(self.env[PL])._nc_compute_lines}

        def counting(model):
            def inner(wizard, *args, **kwargs):
                calls.append(wizard._name)
                return originals[model](wizard, *args, **kwargs)
            return inner

        with patch.object(type(self.env[BS]), '_nc_compute_lines', counting(BS)), \
                patch.object(type(self.env[PL]), '_nc_compute_lines', counting(PL)):
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

    # --------------------------------------------------------------------- Net Profit and Expenses
    def test_expenses_and_net_profit_are_the_profit_and_loss_rows(self):
        self._post(self.company_data, self.company_data['default_account_receivable'],
                   self.company_data['default_account_revenue'], 1000.0)
        self._post(self.company_data, self.company_data['default_account_expense'],
                   self.company_data['default_account_payable'], 300.0)
        _payload, widgets = self._widgets(self.accountant)
        pl_rows = {r['key']: r for r in widgets['profit_loss_summary']['rows'] if r['key']}
        self.assertAlmostEqual(widgets['net_profit']['value'], pl_rows['net_profit']['value'])
        self.assertAlmostEqual(widgets['net_profit']['value'], 700.0)
        self.assertEqual(widgets['net_profit']['tone'], 'positive')
        self.assertAlmostEqual(widgets['expenses']['value'], 300.0)
        # and against an independent read of the ledger
        lines = self.env['account.move.line'].search([
            ('parent_state', '=', 'posted'), ('company_id', '=', self.env.company.id),
            ('date', '>=', self.today.replace(month=1, day=1)), ('date', '<=', self.today),
            ('account_id.account_type', 'in', ('expense', 'expense_other', 'expense_direct_cost',
                                               'expense_depreciation'))])
        self.assertAlmostEqual(widgets['expenses']['value'], sum(lines.mapped('balance')), places=2)

    def test_the_financial_cards_read_together_and_a_non_financial_role_gets_none_of_them(self):
        _payload, widgets = self._widgets(self.accountant)
        keys = list(widgets)
        self.assertEqual(keys.index('expenses'), keys.index('cash_bank') + 1)
        self.assertEqual(keys.index('net_profit'), keys.index('expenses') + 1)
        _payload, employee = self._widgets(self.employee)
        for key in ('expenses', 'net_profit', 'cash_bank', 'receivables'):
            self.assertNotIn(key, employee)

    # --------------------------------------------------------------------- drill-down
    def _drill(self, widget, row, user=None):
        service = self.env['ncollection.report.service']
        if user:
            service = service.with_user(user)
        return service.nc_report_drill(
            widget['wizard']['model'], widget['wizard']['id'], account_ids=row['account_ids'], label=row['label'])

    def test_every_statement_amount_opens_exactly_the_journal_items_it_is_made_of(self):
        self._post(self.company_data, self.company_data['default_account_receivable'],
                   self.company_data['default_account_revenue'], 1000.0)
        self._post(self.company_data, self.company_data['default_account_expense'],
                   self.company_data['default_account_payable'], 300.0)
        _payload, widgets = self._widgets(self.accountant)
        for key in ('balance_sheet_summary', 'profit_loss_summary'):
            widget = widgets[key]
            clickable = [r for r in widget['rows'] if r.get('account_ids')]
            self.assertTrue(clickable, key)
            for row in clickable:
                action = self._drill(widget, row, self.accountant)
                self.assertEqual(action['res_model'], 'account.move.line')
                lines = self.env['account.move.line'].with_user(self.accountant).search(action['domain'])
                # the opened list is the row's own amount: debit-normal rows sum as is, credit-normal rows negated
                if row['key'] in ('net_profit', 'gross_profit', 'liabilities_equity_total'):
                    continue                                       # running results are signed per section
                if row['level'] >= 1 and row['key'] not in ('revenue', 'other_income'):
                    self.assertAlmostEqual(abs(sum(lines.mapped('balance'))), abs(row['value']), places=2,
                                           msg='%s / %s' % (key, row['label']))

    def test_net_profit_opens_every_profit_and_loss_item(self):
        self._post(self.company_data, self.company_data['default_account_receivable'],
                   self.company_data['default_account_revenue'], 1000.0)
        self._post(self.company_data, self.company_data['default_account_expense'],
                   self.company_data['default_account_payable'], 300.0)
        _payload, widgets = self._widgets(self.accountant)
        drill = widgets['net_profit']['drill']
        action = self.env['ncollection.report.service'].with_user(self.accountant).nc_report_drill(
            drill['wizard']['model'], drill['wizard']['id'], account_ids=drill['account_ids'], label=drill['label'])
        lines = self.env['account.move.line'].with_user(self.accountant).search(action['domain'])
        self.assertAlmostEqual(-sum(lines.mapped('balance')), 700.0, places=2)

    def test_operational_cards_open_the_same_domain_the_figure_counts(self):
        self._post(self.company_data, self.company_data['default_account_receivable'],
                   self.company_data['default_account_revenue'], 500.0)
        dash = self.env[DASH].with_user(self.accountant)
        action = dash.drill_kpi('cash_bank')
        self.assertEqual(action['res_model'], 'account.move.line')
        self.assertEqual(action['domain'], dash._domain_cash_bank())
        action = dash.drill_kpi('receivables')
        self.assertEqual(action['domain'], dash._domain_residual('asset_receivable'))

    def test_a_user_without_the_role_or_the_model_cannot_drill_a_figure(self):
        from odoo.exceptions import AccessError
        with self.assertRaises(AccessError):
            self.env[DASH].with_user(self.employee).drill_kpi('cash_bank')         # not an Employee widget
        with self.assertRaises(AccessError):
            self.env[DASH].with_user(self.accountant).drill_kpi('does_not_exist')

    def test_statement_drill_refuses_a_foreign_user_a_foreign_company_and_an_expired_run(self):
        from odoo.exceptions import AccessError, UserError
        self._post(self.company_data, self.company_data['default_account_receivable'],
                   self.company_data['default_account_revenue'], 1000.0)
        _payload, widgets = self._widgets(self.accountant)
        widget = widgets['profit_loss_summary']
        row = next(r for r in widget['rows'] if r.get('account_ids'))
        # the run belongs to the accountant: an employee is refused at the model's own access check
        with self.assertRaises(AccessError):
            self._drill(widget, row, self.employee)
        # a run that no longer exists is an explanation, not a stack trace
        with self.assertRaises(UserError):
            self.env['ncollection.report.service'].with_user(self.accountant).nc_report_drill(
                widget['wizard']['model'], 999999999, account_ids=row['account_ids'])
        # a row with no scope is refused rather than opening every journal item
        with self.assertRaises(UserError):
            self.env['ncollection.report.service'].with_user(self.accountant).nc_report_drill(
                widget['wizard']['model'], widget['wizard']['id'])

    def test_a_user_who_cannot_read_journal_items_gets_no_clickable_rows(self):
        """Statement rows are only drillable for a user who could open the list they lead to."""
        _payload, widgets = self._widgets(self.accountant)
        self.assertTrue(widgets['profit_loss_summary']['wizard'])
        report_only = self._user('nc_sum_noaml', ['ncollection_core.group_role_ceo'])
        with patch.object(type(self.env[DASH]), '_model_readable',
                          lambda self, model: model != 'account.move.line' and model in self.env.registry):
            payload = self.env[DASH].with_user(report_only).get_dashboard_payload()
        pl = next((w for w in payload['widgets'] if w['key'] == 'profit_loss_summary'), None)
        if pl:
            self.assertFalse(pl['wizard'])
            self.assertFalse([r for r in pl['rows'] if r.get('account_ids')])

    def test_zero_balance_accounts_are_left_out_of_the_summary_but_totals_stay(self):
        self._post(self.company_data, self.company_data['default_account_receivable'],
                   self.company_data['default_account_revenue'], 40.0)
        _payload, widgets = self._widgets(self.accountant)
        for widget in (widgets['balance_sheet_summary'], widgets['profit_loss_summary']):
            for row in widget['rows']:
                if row['level'] == 2:
                    self.assertTrue(abs(row['value']) > 0.004, row)
        assets = next(r for r in widgets['balance_sheet_summary']['rows'] if r['key'] == 'assets_total')
        self.assertAlmostEqual(assets['value'], 40.0, places=2)

    # --------------------------------------------------------------------- the payload says what is clickable
    def test_a_card_is_a_button_only_when_the_server_says_it_has_a_readable_destination(self):
        self._post(self.company_data, self.company_data['default_account_receivable'],
                   self.company_data['default_account_revenue'], 300.0)
        _payload, widgets = self._widgets(self.accountant)
        for key in ('cash_bank', 'receivables', 'payables'):
            self.assertEqual(widgets[key]['drill'], {'kind': 'kpi'}, key)
        self.assertEqual(widgets['net_profit']['drill']['kind'], 'accounts')
        _payload, employee = self._widgets(self.employee)
        self.assertEqual(employee['open_activities']['drill'], {'kind': 'kpi'})        # a personal card still opens
        with patch.object(type(self.env[DASH]), '_model_readable',
                          lambda self, model: model != 'account.move.line' and model in self.env.registry):
            payload = self.env[DASH].with_user(self.accountant).get_dashboard_payload()
        cards = {w['key']: w for w in payload['widgets']}
        self.assertNotIn('drill', cards.get('cash_bank', {}))                           # no readable list: no button

    def test_headings_and_totals_no_account_list_can_reproduce_are_not_clickable(self):
        self._post(self.company_data, self.company_data['default_account_receivable'],
                   self.company_data['default_account_revenue'], 800.0)
        _payload, widgets = self._widgets(self.accountant)
        rows = {r['key']: r for r in widgets['balance_sheet_summary']['rows'] if r['key']}
        self.assertNotIn('account_ids', rows['section_assets'])                         # a heading shows 0.00
        self.assertNotIn('account_ids', rows['liabilities_equity_total'])               # includes computed earnings
        self.assertTrue(rows['assets_total'].get('account_ids'))
        pl = {r['key']: r for r in widgets['profit_loss_summary']['rows'] if r['key']}
        self.assertNotIn('account_ids', pl['section_income'])
        self.assertTrue(pl['net_profit'].get('account_ids'))

    def test_the_drill_entry_point_refuses_bad_input_with_a_message_not_a_crash(self):
        from odoo.exceptions import UserError
        _payload, widgets = self._widgets(self.accountant)
        widget = widgets['profit_loss_summary']
        service = self.env['ncollection.report.service'].with_user(self.accountant)
        for bad in ({'account_ids': ['not-a-number']}, {'account_ids': list(range(1, 600))}, {'account_id': 'x'}):
            with self.assertRaises(UserError, msg=str(bad)):
                service.nc_report_drill(widget['wizard']['model'], widget['wizard']['id'], **bad)
        with self.assertRaises(UserError):
            service.nc_report_drill(widget['wizard']['model'], 'x', account_ids=[1])
