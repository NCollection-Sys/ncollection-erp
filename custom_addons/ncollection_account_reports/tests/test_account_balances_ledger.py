# -*- coding: utf-8 -*-
"""Account Balances: Closing = Opening + Debit - Credit, and its drill opens the account's LEDGER (opening row, each
movement in date order, running balance) whose last balance is that same Closing, whatever the display order."""
from datetime import timedelta

from odoo import fields
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.tests import tagged


@tagged('post_install', '-at_install')
class TestAccountBalancesLedger(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.bank = cls.company_data['default_journal_bank'].default_account_id
        cls.revenue = cls.company_data['default_account_revenue']
        cls.expense = cls.company_data['default_account_expense']
        cls.misc = cls.company_data['default_journal_misc']
        cls.fy = cls.env.company.compute_fiscalyear_dates(fields.Date.today())['date_from']
        cls.date_from, cls.date_to = cls.fy + timedelta(days=1), cls.fy + timedelta(days=300)

        def entry(day, debit_account, credit_account, amount, state='posted'):
            move = cls.env['account.move'].create({
                'move_type': 'entry', 'journal_id': cls.misc.id, 'date': cls.fy + timedelta(days=day),
                'line_ids': [(0, 0, {'account_id': debit_account.id, 'debit': amount, 'name': 'd'}),
                             (0, 0, {'account_id': credit_account.id, 'credit': amount, 'name': 'c'})]})
            if state == 'posted':
                move.action_post()
            return move
        entry(0, cls.bank, cls.revenue, 1000.0)          # BEFORE the period: the opening
        entry(5, cls.expense, cls.bank, 300.0)
        entry(9, cls.bank, cls.revenue, 50.0)
        entry(9, cls.expense, cls.bank, 20.0)            # same day: ties by id
        entry(12, cls.bank, cls.revenue, 999.0, state='draft')   # never in a posted report

    def _run(self, model, **kw):
        vals = {'date_from': self.date_from, 'date_to': self.date_to, 'target_move': 'posted', **kw}
        return self.env[model].create(vals)

    def test_closing_is_opening_plus_debit_minus_credit(self):
        rows = {r['account_id']: r for r in self._run('ncollection.account.report.reference')._nc_compute_lines()}
        bank = rows[self.bank.id]
        self.assertAlmostEqual(bank['opening'], 1000.0)
        self.assertAlmostEqual(bank['debit'], 50.0)
        self.assertAlmostEqual(bank['credit'], 320.0)
        self.assertAlmostEqual(bank['balance'], 730.0)
        total = rows[False]
        self.assertAlmostEqual(total['opening'] + total['debit'] - total['credit'], total['balance'], 2)

    def test_the_drill_opens_the_ledger_that_ends_on_the_same_closing(self):
        wizard = self._run('ncollection.account.report.reference')
        action = self.env['ncollection.report.service'].nc_report_drill(
            'ncollection.account.report.reference', wizard.id, account_id=self.bank.id, label='bank')
        self.assertEqual(action['type'], 'ir.actions.client')
        self.assertEqual(action['context']['nc_report_model'], 'ncollection.account.report.general.ledger')
        filters = action['context']['nc_report_filters']
        self.assertEqual((filters['account_ids'], filters['date_from']), ([self.bank.id], str(self.date_from)))
        ledger = self._run('ncollection.account.report.general.ledger', account_ids=[(6, 0, [self.bank.id])])
        rows = ledger._nc_gl_rows()
        self.assertTrue(rows[0]['is_initial'])
        self.assertAlmostEqual(rows[0]['running_balance'], 1000.0)
        running = [r['running_balance'] for r in rows]
        self.assertEqual(running, [1000.0, 700.0, 750.0, 730.0], 'chronological, ties by id, draft excluded')
        dates = [r['date'] for r in rows[1:]]
        self.assertEqual(dates, sorted(dates))
        closing = {r['account_id']: r for r in wizard._nc_compute_lines()}[self.bank.id]['balance']
        self.assertAlmostEqual(rows[-1]['running_balance'], closing)

    def test_a_filter_never_resets_the_opening(self):
        """Narrowing the period keeps all earlier activity in the opening (no reset by a date filter)."""
        later = self._run('ncollection.account.report.reference', date_from=self.fy + timedelta(days=9))
        bank = {r['account_id']: r for r in later._nc_compute_lines()}[self.bank.id]
        self.assertAlmostEqual(bank['opening'], 700.0)
        self.assertAlmostEqual(bank['balance'], 730.0)
