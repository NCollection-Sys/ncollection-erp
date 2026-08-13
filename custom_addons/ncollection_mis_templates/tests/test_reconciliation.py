# -*- coding: utf-8 -*-
"""Balance Sheet / P&L reconciliation (P3-T10).

Acceptance: the MIS reports reconcile to the penny with the General Ledger. The
grouping is by account_type (chart-agnostic), so a posted, balanced set of
entries must yield TOTAL ASSETS == TOTAL LIABILITIES & EQUITY.

Two checks:
  1. The account_type partition used by the templates balances against the raw
     GL (proves no account type is dropped or double-counted).
  2. The actual MIS Balance Sheet instance computes to a balanced sheet (proves
     the MIS expressions — the bale[][account_type domain] syntax — are correct).
"""
from odoo import fields
from odoo.tests import TransactionCase, tagged

# The same account_type groupings the templates use (kept in sync by intent).
_ASSET = ('asset_receivable', 'asset_cash', 'asset_current', 'asset_prepayments',
          'asset_non_current', 'asset_fixed')
_LIAB = ('liability_payable', 'liability_credit_card', 'liability_current',
         'liability_non_current')
_EQUITY = ('equity', 'equity_unaffected')
# `expense_other` added by #411. It was missing from this shadow map AND from
# the template it shadows, and this suite passed anyway — because no fixture
# posted to such an account. A map kept "in sync by intent" with nothing
# exercising the difference is not verified, it is only untested, so the fixture
# below now posts one.
_EARNINGS = ('income', 'income_other', 'expense', 'expense_other',
             'expense_direct_cost', 'expense_depreciation')


@tagged('post_install', '-at_install')
class TestMisReconciliation(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        Account = cls.env['account.account']

        def acc(*types):
            # Odoo 19: account.account is multi-company (no company_id field);
            # searching by type is enough for a single-company test.
            return Account.search([('account_type', 'in', types)], limit=1)

        cls.cash = acc('asset_cash', 'asset_current')
        cls.income = acc('income')
        cls.liab = acc('liability_current', 'liability_payable')
        # #411: created rather than searched — a chart of accounts need not
        # ship an `expense_other` account, and silently skipping the type this
        # suite failed to notice would repeat the original mistake.
        cls.other_expense = Account.create({
            'name': 'FX Loss (#411)', 'code': 'NCMFX1',
            'account_type': 'expense_other',
            'company_ids': [(6, 0, cls.company.ids)]})
        cls.journal = cls.env['account.journal'].search(
            [('type', '=', 'general'), ('company_id', '=', cls.company.id)], limit=1)
        # These require a chart of accounts on the company (loaded by the billing
        # module's post-init in CI). Guard with a clear message if absent.
        assert cls.cash and cls.income and cls.liab and cls.journal, \
            "test needs a chart of accounts on the company (asset/income/liability + a general journal)"

        # One balanced entry spanning asset / income / liability:
        #   Dr cash 1000 + Dr other-expense 50 = Cr revenue 700 + Cr liability 350
        # => assets 1000; current earnings 700 - 50 = 650; liabilities 350;
        #    identity holds ONLY if expense_other is inside earnings (#411).
        move = cls.env['account.move'].create({
            'move_type': 'entry',
            'journal_id': cls.journal.id,
            'date': fields.Date.context_today(cls.env.user),
            'line_ids': [
                (0, 0, {'account_id': cls.cash.id, 'debit': 1000.0, 'credit': 0.0}),
                (0, 0, {'account_id': cls.other_expense.id, 'debit': 50.0,
                        'credit': 0.0}),
                (0, 0, {'account_id': cls.income.id, 'debit': 0.0, 'credit': 700.0}),
                (0, 0, {'account_id': cls.liab.id, 'debit': 0.0, 'credit': 350.0}),
            ],
        })
        move.action_post()
        cls.move = move

    def _gl_by_type(self):
        self.env.cr.execute(
            """
            SELECT a.account_type, SUM(l.balance)
              FROM account_move_line l
              JOIN account_account a ON a.id = l.account_id
              JOIN account_move m ON m.id = l.move_id
             WHERE m.state = 'posted' AND l.company_id = %s
             GROUP BY a.account_type
            """, (self.company.id,))
        return dict(self.env.cr.fetchall())

    def test_account_type_partition_balances_gl(self):
        bal = self._gl_by_type()
        assets = sum(bal.get(t, 0.0) for t in _ASSET)
        liab = -sum(bal.get(t, 0.0) for t in _LIAB)
        equity = -sum(bal.get(t, 0.0) for t in _EQUITY)
        earnings = -sum(bal.get(t, 0.0) for t in _EARNINGS)
        self.assertAlmostEqual(assets, 1000.0, places=2)
        self.assertAlmostEqual(
            assets, liab + equity + earnings, places=2,
            msg="Assets must equal Liabilities + Equity + Current-Year Earnings")

    def test_mis_balance_sheet_reconciles(self):
        instance = self.env.ref('ncollection_mis_templates.mis_report_instance_bs')
        matrix = instance._compute_matrix()
        vals = {}
        for row in matrix.iter_rows():
            if getattr(row, 'account_id', None):
                continue  # skip auto-expanded per-account detail rows
            for cell in row.iter_cells():
                if cell is not None and getattr(cell, 'val', None) is not None:
                    vals[row.kpi.name] = cell.val
                    break
        self.assertIn('total_assets', vals)
        self.assertIn('total_liab_equity', vals)
        self.assertAlmostEqual(vals['total_assets'], 1000.0, places=2)
        self.assertAlmostEqual(
            vals['total_assets'], vals['total_liab_equity'], places=2,
            msg="MIS Balance Sheet must reconcile: assets == liabilities & equity")
