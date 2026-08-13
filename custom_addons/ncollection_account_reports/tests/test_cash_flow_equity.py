# -*- coding: utf-8 -*-
"""F2-T04: Cash Flow Statement + Statement of Changes in Equity (#114).

THE TWO ASSERTIONS THAT MATTER are the acceptance criteria, and both are
comparisons against an INDEPENDENTLY computed figure rather than a restatement
of the code under test:

* "cash flow reconciles with bank/cash account movements" — the three sections
  are summed from non-cash accounts; the movement they are compared against is
  measured on the cash accounts themselves.
* "equity statement ties to BS equity section" — the tie is asserted against
  ``balance_sheet._nc_bucket_totals()``, a separate implementation with its own
  account-type map.

Every figure below is computed by hand from the fixture in the comment beside
it. A report that renders beautifully and is financially wrong is worse than one
that crashes, because nobody notices.
"""
import base64
from datetime import date
from unittest.mock import patch

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from ..wizard import cash_flow as cf


@tagged('post_install', '-at_install')
class TestCashFlowAndEquity(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.CF = cls.env['ncollection.account.report.cash.flow']
        cls.EQ = cls.env['ncollection.account.report.equity.changes']
        cls.BS = cls.env['ncollection.account.report.balance.sheet']
        cls.receivable = cls.company_data['default_account_receivable']
        cls.revenue = cls.company_data['default_account_revenue']
        cls.expense = cls.company_data['default_account_expense']
        cls.journal = cls.company_data['default_journal_misc']

        def account(code, name, account_type):
            return cls.env['account.account'].create({
                'code': code, 'name': name, 'account_type': account_type,
                'company_ids': [(6, 0, cls.env.company.ids)]})

        # Own accounts rather than the fixture's: every account type this
        # statement classifies differently must actually be present, or the
        # sections would all read zero and the reconciliation would pass by
        # measuring nothing.
        cls.bank = account('NCB001', 'Bank', 'asset_cash')
        cls.equipment = account('NCF001', 'Equipment', 'asset_fixed')
        cls.accum_dep = account('NCF002', 'Accumulated Depreciation', 'asset_fixed')
        cls.dep_expense = account('NCD001', 'Depreciation Expense',
                                  'expense_depreciation')
        cls.loan = account('NCL001', 'Bank Loan', 'liability_non_current')
        cls.capital = account('NCE001', 'Share Capital', 'equity')

        def entry(day, debit_account, credit_account, amount):
            move = cls.env['account.move'].create({
                'move_type': 'entry', 'journal_id': cls.journal.id, 'date': day,
                'line_ids': [
                    (0, 0, {'account_id': debit_account.id, 'debit': amount,
                            'credit': 0.0}),
                    (0, 0, {'account_id': credit_account.id, 'debit': 0.0,
                            'credit': amount}),
                ]})
            move.action_post()
            return move

        # --- the fixture, and the whole of it -----------------------------
        # 2025 (before the period): capital injected in cash.
        entry(date(2025, 12, 31), cls.bank, cls.capital, 5000.0)   # cash 5,000
        # 2026 (the period):
        entry(date(2026, 2, 1), cls.receivable, cls.revenue, 10000.0)  # no cash
        entry(date(2026, 3, 1), cls.bank, cls.receivable, 6000.0)      # +6,000
        entry(date(2026, 4, 1), cls.expense, cls.bank, 2000.0)         # -2,000
        entry(date(2026, 5, 1), cls.equipment, cls.bank, 4000.0)       # -4,000
        entry(date(2026, 6, 1), cls.bank, cls.loan, 3000.0)            # +3,000
        entry(date(2026, 12, 31), cls.dep_expense, cls.accum_dep, 1000.0)  # non-cash

        cls.date_from = date(2026, 1, 1)
        cls.date_to = date(2026, 12, 31)

    def _cf(self, **overrides):
        values = {'date_from': self.date_from, 'date_to': self.date_to,
                  'target_move': 'posted', 'comparison_type': 'none'}
        values.update(overrides)
        return self.CF.create(values)

    def _eq(self, **overrides):
        values = {'date_from': self.date_from, 'date_to': self.date_to,
                  'target_move': 'posted'}
        values.update(overrides)
        return self.EQ.create(values)

    # ---- AC1: cash flow reconciles with cash-account movement -----------

    def test_the_sections_equal_the_movement_on_the_cash_accounts(self):
        """THE acceptance criterion.

        Left side: operating + investing + financing, summed from the NON-cash
        accounts. Right side: the movement on the cash accounts, measured on
        those accounts. Nothing in common but double entry.
        """
        figures = self._cf()._nc_totals()
        cash_movement = figures['cash_close'] - figures['cash_open']
        self.assertAlmostEqual(cash_movement, 3000.0, places=2,
                               msg="fixture drifted: +6000 -2000 -4000 +3000")
        self.assertAlmostEqual(
            figures['operating'] + figures['investing'] + figures['financing'],
            cash_movement, places=2,
            msg="the three sections do not add up to the movement on cash — "
                "the statement does not reconcile")

    def test_each_section_is_the_hand_computed_figure(self):
        """Reconciliation alone would also pass if every section were zero, or
        if two errors cancelled. These are the individual numbers."""
        figures = self._cf()._nc_totals()
        # profit 7,000 + depreciation 1,000 - receivables growth 4,000
        self.assertAlmostEqual(figures['operating'], 4000.0, places=2)
        # equipment 4,000 out, less the 1,000 depreciation that never moved cash
        self.assertAlmostEqual(figures['investing'], -4000.0, places=2)
        self.assertAlmostEqual(figures['financing'], 3000.0, places=2)
        # 10,000 revenue - 2,000 expense - 1,000 depreciation
        self.assertAlmostEqual(figures['net_profit'], 7000.0, places=2)
        self.assertAlmostEqual(figures['depreciation'], 1000.0, places=2)
        # receivables grew by 10,000 - 6,000 and that growth consumed cash
        self.assertAlmostEqual(figures['working_capital'], -4000.0, places=2)
        self.assertAlmostEqual(figures['cash_open'], 5000.0, places=2)
        self.assertAlmostEqual(figures['cash_close'], 8000.0, places=2)

    def test_depreciation_is_moved_between_sections_and_not_invented(self):
        """The add-back is the one presentation choice that shifts money
        between sections. It must be exactly equal and opposite: operating up by
        the depreciation, investing down by the same, total untouched."""
        figures = self._cf()._nc_totals()
        depreciation = figures['depreciation']
        self.assertGreater(depreciation, 0.0, "fixture posted no depreciation, "
                                              "so this test proves nothing")
        # Without the add-back operating would be 3,000 and investing -3,000.
        self.assertAlmostEqual(figures['operating'] - depreciation, 3000.0, places=2)
        self.assertAlmostEqual(figures['investing'] + depreciation, -3000.0, places=2)
        self.assertAlmostEqual(figures['net_change'],
                               figures['cash_close'] - figures['cash_open'],
                               places=2)

    def test_the_statement_ends_on_the_two_cash_balances(self):
        """The reconciliation has to be checkable BY A READER, not only by this
        test — so opening and closing cash are on the face of the statement and
        their difference is the Net Change line above them."""
        rows = {row['label']: row for row in self._cf()._nc_compute_lines()}
        opening = rows["Cash at the beginning of the period"]['current_amount']
        closing = rows["Cash at the end of the period"]['current_amount']
        net_change = rows["NET CHANGE IN CASH"]['current_amount']
        self.assertAlmostEqual(closing - opening, net_change, places=2)

    def test_the_comparison_period_is_computed_over_its_own_window(self):
        """Previous-period figures must come from the previous period. Reusing
        the current figures would render as "no change" and look plausible."""
        wizard = self._cf(comparison_type='previous_year')
        rows = {row['label']: row for row in wizard._nc_compute_lines()}
        net_profit = rows["Net profit for the period"]
        self.assertAlmostEqual(net_profit['current_amount'], 7000.0, places=2)
        # 2025 held one capital injection and no P&L activity at all.
        self.assertAlmostEqual(net_profit['previous_amount'], 0.0, places=2)
        self.assertAlmostEqual(net_profit['variance'], 7000.0, places=2)

    # ---- the guards the identity rests on -------------------------------

    def test_every_odoo_account_type_is_classified_exactly_once(self):
        """The identity holds only if no type is dropped or double-counted."""
        buckets = (cf.CASH, cf.OPERATING, cf.INVESTING, cf.FINANCING, cf.EXCLUDED)
        for account_type, _label in self.env['account.account']._fields[
                'account_type'].selection:
            hits = [b for b in buckets if account_type in b]
            self.assertEqual(len(hits), 1,
                             "%s is in %d cash-flow buckets" % (account_type,
                                                                len(hits)))

    def test_an_unclassified_type_refuses_to_render(self):
        """Mutation proof: drop a type from the map and the statement must
        REFUSE. Without this the guard could be dead code and nothing would say
        so — the report would just silently omit a category."""
        without_income = tuple(t for t in cf.OPERATING if t != 'income')
        with patch.object(cf, 'OPERATING', without_income), \
                patch.object(cf, 'SECTIONS',
                             (('operating', without_income),
                              ('investing', cf.INVESTING),
                              ('financing', cf.FINANCING))):
            with self.assertRaises(UserError):
                self._cf()._nc_compute_lines()

    def test_a_double_classified_type_refuses_to_render(self):
        """The other way the identity breaks: a type counted in two sections."""
        with patch.object(cf, 'INVESTING', cf.INVESTING + ('income',)):
            with self.assertRaises(UserError):
                self._cf()._nc_compute_lines()

    def test_a_filtered_cash_flow_refuses_rather_than_misreporting(self):
        """A journal/account/partner filter leaves the two sides of the
        identity looking at different subsets of the books."""
        for field, value in (('journal_ids', self.journal.ids),
                             ('account_ids', self.bank.ids),
                             ('partner_ids', self.partner_a.ids)):
            with self.subTest(filter=field):
                wizard = self._cf(**{field: [(6, 0, value)]})
                with self.assertRaises(UserError):
                    wizard._nc_compute_lines()

    def test_the_filter_guard_covers_the_export_channels_too(self):
        """PDF and XLSX go through _nc_compute_lines as well — a guard that only
        protected the on-screen list would let the exported document lie."""
        wizard = self._cf(journal_ids=[(6, 0, self.journal.ids)])
        with self.assertRaises(UserError):
            wizard._nc_build_xlsx()

    # ---- AC2: the equity statement ties to the BS equity section --------

    def test_equity_closing_ties_to_the_balance_sheet_equity_section(self):
        """THE second acceptance criterion, against the Balance Sheet's OWN
        computation (its own account-type map, its own aggregate)."""
        _opening, _movement, closing = self._eq()._nc_totals()
        bs = self.BS.create({
            'date_from': self.date_from, 'date_to': self.date_to,
            'target_move': 'posted', 'comparison_type': 'none'})
        bs_totals = bs._nc_bucket_totals(bs)[0]
        bs_equity = bs_totals['equity'] + bs_totals['accumulated_earnings']
        self.assertAlmostEqual(closing, bs_equity, places=2,
                               msg="the equity statement does not tie to the "
                                   "Balance Sheet's equity section")
        self.assertAlmostEqual(closing, 12000.0, places=2)  # 5,000 + 7,000

    def test_opening_plus_movement_equals_closing(self):
        opening, movement, closing = self._eq()._nc_totals()
        self.assertAlmostEqual(opening, 5000.0, places=2)     # capital only
        self.assertAlmostEqual(movement, 7000.0, places=2)    # the period's profit
        self.assertAlmostEqual(opening + movement, closing, places=2)

    def test_the_movement_in_accumulated_result_is_the_periods_profit(self):
        """The line a reader checks against the P&L. Compared with the cash
        flow's independently computed net profit, not with itself."""
        rows = {row['label']: row for row in self._eq()._nc_compute_lines()}
        self.assertAlmostEqual(rows["Accumulated Result"]['balance'],
                               self._cf()._nc_totals()['net_profit'], places=2)

    def test_components_carry_per_account_detail_for_drill_down(self):
        """A total with no way to see what is inside it is where a wrong number
        hides. Every component row must be followed by its accounts."""
        rows = self._eq()._nc_compute_lines()
        detail = {row['account_id'] for row in rows if row['level'] == 2}
        self.assertIn(self.capital.id, detail)
        self.assertIn(self.revenue.id, detail)
        # ...and the detail adds up to the component above it.
        capital_rows = [row for row in rows
                        if row['level'] == 2 and row['account_id'] == self.capital.id]
        self.assertAlmostEqual(sum(row['closing_balance'] for row in capital_rows),
                               5000.0, places=2)

    def test_equity_drill_down_matches_the_displayed_closing_balance(self):
        """Closing is cumulative, so a period-bounded drill-down would open a
        list whose total is smaller than the row it came from."""
        self._eq().action_view()
        line = self.env['ncollection.account.report.line'].search(
            [('account_id', '=', self.capital.id)], limit=1)
        drilled = sum(self.env['account.move.line'].search(
            line.action_drill_down()['domain']).mapped('balance'))
        # credit-normal, so the statement negates what the ledger shows
        self.assertAlmostEqual(line.closing_balance, -drilled, places=2)

    def test_a_filtered_equity_statement_refuses(self):
        wizard = self._eq(account_ids=[(6, 0, self.capital.ids)])
        with self.assertRaises(UserError):
            wizard._nc_compute_lines()

    def test_the_bs_tie_diverges_only_by_expense_other_and_by_that_much(self):
        """CHARACTERISATION, not approval.

        The Balance Sheet's ``accumulated_earnings`` bucket is a deliberate 1:1
        transcription of the mis_builder template it replaces until #117, and
        that expression omits ``expense_other``. This statement does not omit
        it, because equity that drops a class of expense is wrong. So on books
        that use such an account the two differ — by exactly its balance, which
        is what this pins down. Reconciling them means editing the mis parity
        map, which belongs to #117.

        The cash flow, which classifies every type, keeps reconciling — that
        contrast is the whole argument for CLASSIFY-EVERYTHING.
        """
        fx_loss = self.env['account.account'].create({
            'code': 'NCX001', 'name': 'FX Loss', 'account_type': 'expense_other',
            'company_ids': [(6, 0, self.env.company.ids)]})
        move = self.env['account.move'].create({
            'move_type': 'entry', 'journal_id': self.journal.id,
            'date': date(2026, 7, 1),
            'line_ids': [
                (0, 0, {'account_id': fx_loss.id, 'debit': 500.0, 'credit': 0.0}),
                (0, 0, {'account_id': self.bank.id, 'debit': 0.0, 'credit': 500.0}),
            ]})
        move.action_post()

        closing = self._eq()._nc_totals()[2]
        bs = self.BS.create({
            'date_from': self.date_from, 'date_to': self.date_to,
            'target_move': 'posted', 'comparison_type': 'none'})
        bs_totals = bs._nc_bucket_totals(bs)[0]
        bs_equity = bs_totals['equity'] + bs_totals['accumulated_earnings']
        self.assertAlmostEqual(bs_equity - closing, 500.0, places=2,
                               msg="the BS/equity divergence is no longer "
                                   "exactly the expense_other balance — #117 "
                                   "may have closed it, or something else moved")
        # The cash flow is unaffected: expense_other IS classified there.
        figures = self._cf()._nc_totals()
        self.assertAlmostEqual(
            figures['operating'] + figures['investing'] + figures['financing'],
            figures['cash_close'] - figures['cash_open'], places=2)

    # ---- AC3: export, plumbing, access ----------------------------------

    def test_pdf_and_xlsx_render_for_both_reports(self):
        for wizard in (self._cf(comparison_type='previous_year'), self._eq()):
            with self.subTest(report=wizard._name):
                action = wizard.action_export_pdf()
                self.assertEqual(action['type'], 'ir.actions.report')
                # #250: the export streams from a controller and persists
                # nothing, so the workbook is checked at the source.
                xlsx = wizard.action_export_xlsx()
                self.assertIn('/ncollection/account_reports/xlsx/', xlsx['url'])
                self.assertTrue(
                    base64.b64decode(wizard._nc_build_xlsx()).startswith(b'PK'))

    def test_each_report_has_its_own_report_action(self):
        """Two ir.actions.report sharing a report_name render every wizard
        against the FIRST action's model — shipped once, found the hard way."""
        names = set()
        for wizard in (self._cf(), self._eq()):
            report = self.env.ref(wizard._nc_report_action_ref())
            self.assertEqual(report.model, wizard._name)
            names.add(report.report_name)
        self.assertEqual(len(names), 2, names)
        for ref in ('action_report_balance_sheet', 'action_report_profit_loss'):
            shipped = self.env.ref('ncollection_account_reports.%s' % ref)
            self.assertNotIn(shipped.report_name, names)

    def test_action_view_carries_the_figures_into_the_line_records(self):
        """The list must show what _nc_compute_lines computed — the engine
        dropping a key renders a column of zeros, which reads as real data."""
        action = self._eq().action_view()
        lines = self.env['ncollection.account.report.line'].browse(
            action['domain'][0][2])
        total = lines.filtered(lambda line: line.label == 'TOTAL EQUITY')
        self.assertAlmostEqual(total.opening_balance, 5000.0, places=2)
        self.assertAlmostEqual(total.balance, 7000.0, places=2)
        self.assertAlmostEqual(total.closing_balance, 12000.0, places=2)
        self.assertEqual(
            action['views'][0][0],
            self.env.ref('ncollection_account_reports.'
                         'view_report_line_equity_list').id,
            "the equity list view is not pinned — Odoo may render the report "
            "through a view with none of its columns")

    def test_a_user_without_the_accounting_group_is_denied(self):
        """Mirrors every sibling report: the ACL gates on
        account.group_account_readonly."""
        user = self.env['res.users'].create({
            'name': 'No Accounting', 'login': 'f2t04_noacc',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        for model in (self.CF, self.EQ):
            with self.subTest(model=model._name):
                with self.assertRaises(AccessError):
                    model.with_user(user).create({
                        'date_from': self.date_from, 'date_to': self.date_to,
                        'target_move': 'posted'})
