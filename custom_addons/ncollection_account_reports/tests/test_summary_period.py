# -*- coding: utf-8 -*-
"""The Summary's From / To period: what each figure means by it, and that it never widens what is visible.

Synthetic data only. The period is the single source of truth for the figures that are about a period; the figures
that are a current state (receivables, cash...) or a fixed window ("this month") do not move with it.
"""
from datetime import date, timedelta

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import UserError
from odoo.tests import tagged

DASH = 'ncollection.dashboard.data'
PL = 'ncollection.account.report.profit.loss'
BS = 'ncollection.account.report.balance.sheet'


@tagged('post_install', '-at_install')
class TestSummaryPeriod(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.today = date.today()
        cls.year = cls.today.year
        cls.accountant = cls.env['res.users'].create({
            'name': 'nc_per_acct', 'login': 'nc_per_acct', 'company_id': cls.env.company.id,
            'company_ids': [(6, 0, cls.env.company.ids)],
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id,
                                  cls.env.ref('ncollection_core.group_role_accountant').id,
                                  cls.env.ref('account.group_account_readonly').id])]})
        # income on three known days, all inside the current year (and not in the future)
        cls.d1, cls.d2, cls.d3 = date(cls.year, 1, 10), date(cls.year, 1, 20), date(cls.year, 1, 30)
        for when, amount in ((cls.d1, 100.0), (cls.d2, 400.0), (cls.d3, 1000.0)):
            cls._income(when, amount)

    @classmethod
    def _income(cls, when, amount):
        data = cls.company_data
        move = cls.env['account.move'].create({
            'move_type': 'entry', 'journal_id': data['default_journal_misc'].id, 'date': when,
            'line_ids': [
                (0, 0, {'account_id': data['default_account_receivable'].id, 'debit': amount, 'credit': 0.0}),
                (0, 0, {'account_id': data['default_account_revenue'].id, 'debit': 0.0, 'credit': amount})]})
        move.action_post()

    def _payload(self, date_from=None, date_to=None):
        payload = self.env[DASH].with_user(self.accountant).get_dashboard_payload(date_from, date_to)
        return payload, {w['key']: w for w in payload['widgets']}

    def _net_profit(self, widgets):
        return next(r['value'] for r in widgets['profit_loss_summary']['rows'] if r['key'] == 'net_profit')

    # ------------------------------------------------------------------ the default
    def test_the_default_is_the_current_year_to_today(self):
        payload, widgets = self._payload()
        period = payload['meta']['period']
        self.assertEqual((period['date_from'], period['date_to'], period['is_default']),
                         (str(date(self.year, 1, 1)), str(self.today), True))
        wizard = self.env[PL].with_user(self.accountant).create({
            'date_from': date(self.year, 1, 1), 'date_to': self.today, 'target_move': 'posted',
            'comparison_type': 'none'})
        wizard.exclude_zero = True
        engine = next(r['current_amount'] for r in wizard._nc_compute_lines() if r['key'] == 'net_profit')
        self.assertEqual(self._net_profit(widgets), engine)
        self.assertEqual(widgets['net_profit']['sub'], 'Year to date')

    # ------------------------------------------------------------------ a chosen period
    def test_a_custom_period_drives_profit_and_loss_and_the_figures_derived_from_it(self):
        payload, widgets = self._payload(self.d2, self.d3)
        self.assertEqual(self._net_profit(widgets), 1400.0)         # 400 + 1000, not the 100 before From
        self.assertEqual(widgets['net_profit']['value'], 1400.0)
        self.assertEqual(widgets['profit_loss_summary']['period'], '%s → %s' % (self.d2, self.d3))
        self.assertFalse(payload['meta']['period']['is_default'])
        self.assertEqual(widgets['net_profit']['sub'], 'Selected period')
        self.assertEqual(widgets['net_profit']['period'], '%s → %s' % (self.d2, self.d3))

    def test_the_boundary_dates_are_inclusive(self):
        _p, widgets = self._payload(self.d2, self.d2)
        self.assertEqual(self._net_profit(widgets), 400.0, "From == To == a posting date includes that day")
        _p, widgets = self._payload(self.d1 + timedelta(days=1), self.d2 - timedelta(days=1))
        self.assertEqual(self._net_profit(widgets), 0.0, "the days either side are outside")

    def test_the_balance_sheet_is_cumulative_to_the_to_date_only(self):
        def total_assets(widgets):
            return sum(r['value'] for r in widgets['balance_sheet_summary']['rows']
                       if r['key'] == 'assets_total')
        _p, upto_d2 = self._payload(date(self.year, 1, 1), self.d2)
        _p, upto_d3 = self._payload(date(self.year, 1, 1), self.d3)
        _p, from_later = self._payload(self.d2, self.d3)
        self.assertEqual(total_assets(upto_d3) - total_assets(upto_d2), 1000.0)
        self.assertEqual(total_assets(from_later), total_assets(upto_d3), "From does not cut a balance")

    def test_the_revenue_trend_ends_at_the_to_date(self):
        _p, widgets = self._payload(date(self.year, 1, 1), self.d2)
        labels = widgets['revenue_6m']['labels']
        self.assertEqual(len(labels), 6)
        self.assertEqual(labels[-1], self.d2.strftime('%b'))
        self.assertEqual(widgets['revenue_6m']['data'][-1], 500.0, "January up to the 20th: 100 + 400")

    def test_current_state_figures_do_not_move_with_the_period(self):
        _p, default = self._payload()
        _p, custom = self._payload(self.d1, self.d1)
        for key in ('receivables', 'payables', 'cash_bank'):
            self.assertEqual(default[key]['value'], custom[key]['value'], key)

    # ------------------------------------------------------------------ bad input
    def test_bad_periods_are_refused_with_a_message(self):
        service = self.env[DASH].with_user(self.accountant)
        for args in ((self.d3, self.d1), (self.d1, None), (None, self.d1), ('not a date', '2026-01-01'),
                     ('1800-01-01', '1900-01-02'), ('2026-01-01', '2999-01-01')):
            with self.subTest(args=args), self.assertRaises(UserError):
                service.get_dashboard_payload(*args)

    def test_dates_may_arrive_as_text_as_they_do_from_the_browser(self):
        _p, widgets = self._payload(str(self.d2), str(self.d3))
        self.assertEqual(self._net_profit(widgets), 1400.0)

    def test_nothing_at_all_means_the_default_not_an_error(self):
        payload, _widgets = self._payload(None, None)
        self.assertTrue(payload['meta']['period']['is_default'])
        payload, _widgets = self._payload(False, False)
        self.assertTrue(payload['meta']['period']['is_default'])

    # ------------------------------------------------------------------ scope
    def test_a_period_never_widens_the_company_scope(self):
        other = self.setup_other_company()
        data = other['company'] and other
        move = self.env['account.move'].with_company(data['company']).create({
            'move_type': 'entry', 'journal_id': data['default_journal_misc'].id, 'date': self.d2,
            'line_ids': [
                (0, 0, {'account_id': data['default_account_receivable'].id, 'debit': 7777.0, 'credit': 0.0}),
                (0, 0, {'account_id': data['default_account_revenue'].id, 'debit': 0.0, 'credit': 7777.0})]})
        move.action_post()
        _p, widgets = self._payload(date(self.year, 1, 1), self.today)
        self.assertEqual(self._net_profit(widgets), 1500.0,
                         "the other company's 7777 is not in this company's Summary")

    def test_a_non_financial_role_still_gets_no_statement_whatever_the_period(self):
        employee = self.env['res.users'].create({
            'name': 'nc_per_emp', 'login': 'nc_per_emp', 'company_id': self.env.company.id,
            'company_ids': [(6, 0, self.env.company.ids)],
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id,
                                  self.env.ref('ncollection_core.group_role_employee').id])]})
        payload = self.env[DASH].with_user(employee).get_dashboard_payload(self.d1, self.d3)
        keys = {w['key'] for w in payload['widgets']}
        self.assertFalse(keys & {'profit_loss_summary', 'balance_sheet_summary', 'net_profit', 'expenses'})

    def test_two_periods_do_not_share_a_cached_answer(self):
        _p, first = self._payload(self.d1, self.d1)
        _p, second = self._payload(self.d3, self.d3)
        _p, again = self._payload(self.d1, self.d1)
        self.assertEqual(self._net_profit(first), 100.0)
        self.assertEqual(self._net_profit(second), 1000.0)
        self.assertEqual(self._net_profit(again), 100.0)

    # ------------------------------------------------------------------ Arabic
    def test_the_period_captions_follow_the_language(self):
        self.env['res.lang']._activate_lang('ar_001')
        self.env['ir.module.module']._load_module_terms(['ncollection_core'], ['ar_001'], overwrite=True)
        service = self.env[DASH].with_user(self.accountant).with_context(lang='ar_001')
        widgets = {w['key']: w for w in service.get_dashboard_payload()['widgets']}
        self.assertNotEqual(widgets['net_profit']['sub'], 'Year to date')
        english = {w['key']: w for w in self.env[DASH].with_user(self.accountant).with_context(
            lang='en_US').get_dashboard_payload()['widgets']}
        self.assertEqual(english['net_profit']['sub'], 'Year to date')

    def test_the_balance_sheet_says_it_is_as_of_the_to_date(self):
        _p, widgets = self._payload(self.d2, self.d3)
        self.assertEqual(widgets['balance_sheet_summary']['period'], 'As of %s' % self.d3)
        self.assertEqual(widgets['profit_loss_summary']['period'], '%s → %s' % (self.d2, self.d3))
