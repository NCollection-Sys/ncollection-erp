# -*- coding: utf-8 -*-
"""F2-T03: Balance Sheet + Profit & Loss — the balancing identity, the P&L
section arithmetic, comparison periods, and PARITY with the mis_templates
groupings this pair replaces.

Parity approach (decided on #113): the expected ``account_type`` groupings below
are transcribed INDEPENDENTLY from
``ncollection_mis_templates/data/mis_report_*.xml`` and asserted against the
wizards' own maps. A live comparison against a real ``mis.report.instance`` was
rejected deliberately: it would make this module — whose whole purpose is to
OUTLIVE mis_builder (ADR #15, #117) — depend on mis_builder at test time, so the
test proving mis_builder is safe to delete would itself die with it.
"""
import base64
from datetime import date

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from ..wizard.balance_sheet import _BS_SECTIONS
from ..wizard.profit_and_loss import _PL_BUCKETS

# --- independent transcription of the mis KPI expressions -------------------
# mis_report_balance_sheet.xml — `bale[][('account_id.account_type', ...)]`
_MIS_BS = {
    'current_assets': ('asset_receivable', 'asset_cash', 'asset_current',
                       'asset_prepayments'),
    'fixed_assets': ('asset_non_current', 'asset_fixed'),
    'current_liabilities': ('liability_payable', 'liability_credit_card',
                            'liability_current'),
    'long_term_liabilities': ('liability_non_current',),
    'equity': ('equity', 'equity_unaffected'),
    'accumulated_earnings': ('income', 'income_other', 'expense',
                             'expense_direct_cost', 'expense_depreciation'),
}
# mis_report_profit_and_loss.xml — `balp[][('account_id.account_type', ...)]`
_MIS_PL = {
    'revenue': ('income',),
    'other_income': ('income_other',),
    'cogs': ('expense_direct_cost',),
    'operating_expenses': ('expense', 'expense_depreciation'),
}
# Credit-normal groups mis negates (a leading `-` on the expression).
_MIS_NEGATED = {'current_liabilities', 'long_term_liabilities', 'equity',
                'accumulated_earnings', 'revenue', 'other_income'}


@tagged('post_install', '-at_install')
class TestBalanceSheetProfitAndLoss(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.BS = cls.env['ncollection.account.report.balance.sheet']
        cls.PL = cls.env['ncollection.account.report.profit.loss']
        cls.receivable = cls.company_data['default_account_receivable']
        cls.revenue = cls.company_data['default_account_revenue']
        cls.expense = cls.company_data['default_account_expense']
        cls.journal = cls.company_data['default_journal_misc']

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

        # Prior year (2025): revenue 400 — lands in accumulated earnings.
        entry(date(2025, 6, 30), cls.receivable, cls.revenue, 400.0)
        # Current year (2026): revenue 1000, expense 300 → net 700.
        entry(date(2026, 6, 15), cls.receivable, cls.revenue, 1000.0)
        entry(date(2026, 6, 20), cls.expense, cls.receivable, 300.0)

        cls.date_from = date(2026, 1, 1)
        cls.date_to = date(2026, 12, 31)

    def _bs(self, **overrides):
        values = {'date_from': self.date_from, 'date_to': self.date_to,
                  'target_move': 'posted', 'comparison_type': 'none'}
        values.update(overrides)
        return self.BS.create(values)

    def _pl(self, **overrides):
        values = {'date_from': self.date_from, 'date_to': self.date_to,
                  'target_move': 'posted', 'comparison_type': 'none'}
        values.update(overrides)
        return self.PL.create(values)

    # ---- AC1: the balancing identity ------------------------------------

    def test_bs_assets_equal_liabilities_plus_equity(self):
        """Assets = Liabilities + Equity — the acceptance criterion. Holds only
        because Accumulated Earnings carries the un-posted net result."""
        assets, liabilities_equity = self._bs()._nc_totals()
        # Non-vacuity: 0 == 0 would satisfy the identity while proving nothing.
        self.assertNotAlmostEqual(assets, 0.0, places=2)
        self.assertAlmostEqual(assets, liabilities_equity, places=2)

    def test_bs_balances_with_no_comparison_and_with_one(self):
        """The identity must not depend on the comparison setting."""
        for comparison_type in ('none', 'previous_period', 'previous_year'):
            with self.subTest(comparison_type=comparison_type):
                assets, liabilities_equity = self._bs(
                    comparison_type=comparison_type)._nc_totals()
                self.assertAlmostEqual(assets, liabilities_equity, places=2)

    def test_bs_grand_total_rows_agree(self):
        """The two rendered grand-total rows carry equal figures."""
        rows = self._bs()._nc_compute_lines()
        totals = [r for r in rows if r['label'].startswith('TOTAL')]
        self.assertEqual(len(totals), 2)
        self.assertAlmostEqual(totals[0]['current_amount'],
                               totals[1]['current_amount'], places=2)

    def test_bs_is_cumulative_not_period(self):
        """A balance sheet is a POSITION: the prior-year 400 must be included
        even though it falls outside date_from..date_to."""
        assets = self._bs()._nc_totals()[0]
        # receivable 400 + 1000 - 300 = 1100
        self.assertAlmostEqual(assets, 1100.0, places=2)

    # ---- AC2: P&L comparison columns ------------------------------------

    def test_pl_section_arithmetic(self):
        figures = self._pl()._nc_totals()
        self.assertAlmostEqual(figures['revenue'], 1000.0)      # negated to positive
        self.assertAlmostEqual(figures['total_income'],
                               figures['revenue'] + figures['other_income'])
        self.assertAlmostEqual(figures['gross_profit'],
                               figures['total_income'] - figures['cogs'])
        self.assertAlmostEqual(figures['net_profit'],
                               figures['gross_profit'] - figures['operating_expenses'])
        self.assertAlmostEqual(figures['net_profit'], 700.0)    # 1000 - 300

    def test_pl_previous_year_comparison_columns(self):
        """Current 2026 vs the same period in 2025 (revenue 400, no expense)."""
        rows = {r['label']: r for r in
                self._pl(comparison_type='previous_year')._nc_compute_lines()}
        net = rows['NET PROFIT']
        self.assertAlmostEqual(net['current_amount'], 700.0)
        self.assertAlmostEqual(net['previous_amount'], 400.0)
        self.assertAlmostEqual(net['variance'], 300.0)
        self.assertAlmostEqual(net['variance_pct'], 75.0, places=2)

    def test_pl_previous_period_is_same_length_immediately_before(self):
        wizard = self._pl(date_from=date(2026, 4, 1), date_to=date(2026, 6, 30),
                          comparison_type='previous_period')
        start, end = wizard._nc_comparison_range()
        self.assertEqual(end, date(2026, 3, 31))          # day before the report
        self.assertEqual(end - start, date(2026, 6, 30) - date(2026, 4, 1))

    def test_comparison_none_hides_the_comparison_columns(self):
        keys = [c['key'] for c in self._pl()._nc_columns()]
        self.assertNotIn('previous_amount', keys)
        keys = [c['key'] for c in self._pl(comparison_type='previous_year')._nc_columns()]
        self.assertEqual(keys, ['label', 'current_amount', 'previous_amount',
                                'variance', 'variance_pct'])

    # ---- variance edge cases --------------------------------------------

    def test_variance_pct_is_zero_not_infinite_when_previous_is_zero(self):
        variance, pct = self.PL._nc_variance(500.0, 0.0)
        self.assertAlmostEqual(variance, 500.0)
        self.assertEqual(pct, 0.0)

    def test_variance_pct_sign_follows_the_real_move_for_negatives(self):
        """Over a raw signed denominator, -100 -> -150 would read +50%. Over
        abs(previous) it correctly reads -50%."""
        _variance, pct = self.PL._nc_variance(-150.0, -100.0)
        self.assertAlmostEqual(pct, -50.0, places=2)

    def test_custom_comparison_requires_both_dates(self):
        with self.assertRaises(UserError):
            self._pl(comparison_type='custom')._nc_comparison_range()

    def test_custom_comparison_rejects_inverted_range(self):
        with self.assertRaises(UserError):
            self._pl(comparison_type='custom',
                     comparison_date_from=date(2025, 12, 31),
                     comparison_date_to=date(2025, 1, 1))._nc_comparison_range()

    # ---- AC3/AC4: UAE CoA grouping + mis parity --------------------------

    def test_bs_groupings_match_mis_templates(self):
        """Independent transcription vs the wizard's own map."""
        actual = {key: types
                  for _s, _l, buckets, _t in _BS_SECTIONS
                  for key, _bl, _sign, types in buckets}
        self.assertEqual(actual, _MIS_BS)

    def test_pl_groupings_match_mis_templates(self):
        actual = {key: types for key, _l, _sign, types in _PL_BUCKETS}
        self.assertEqual(actual, _MIS_PL)

    def test_credit_normal_groups_are_negated_exactly_as_mis_does(self):
        negated = {key
                   for _s, _l, buckets, _t in _BS_SECTIONS
                   for key, _bl, sign, _t2 in buckets if sign == -1}
        negated |= {key for key, _l, sign, _t in _PL_BUCKETS if sign == -1}
        self.assertEqual(negated, _MIS_NEGATED)

    def test_grouping_uses_account_type_never_code_prefixes(self):
        """UAE CoA (P3-T05 / #45) correctness: every grouping key must be a real
        Odoo account_type selection value, so the reports are chart-agnostic."""
        valid = set(dict(
            self.env['account.account']._fields['account_type'].selection))
        used = {t
                for _s, _l, buckets, _tl in _BS_SECTIONS
                for _k, _bl, _sg, types in buckets for t in types}
        used |= {t for _k, _l, _sg, types in _PL_BUCKETS for t in types}
        self.assertEqual(used - valid, set())

    def test_every_pl_account_type_appears_in_accumulated_earnings(self):
        """The BS 'Accumulated Earnings' bucket must cover exactly the account
        types the P&L reports on — otherwise the statement silently stops
        balancing the moment a chart uses a P&L type nobody listed."""
        pl_types = {t for _k, _l, _sg, types in _PL_BUCKETS for t in types}
        self.assertEqual(pl_types, set(_MIS_BS['accumulated_earnings']))

    # ---- isolation (Rule 4: UI restriction mirrored at the ORM) ----------

    def test_report_runs_are_private_to_their_creator(self):
        other = self.env['res.users'].create({
            'name': 'Other Accountant', 'login': 'nc_other_accountant',
            'group_ids': [(6, 0, [self.env.ref('account.group_account_readonly').id,
                                  self.env.ref('base.group_user').id])],
        })
        mine = self._bs()
        self.assertFalse(
            self.BS.with_user(other).search([('id', '=', mine.id)]),
            "another user could read someone else's Balance Sheet run")

    # ---- drill-down must reconcile with the figure it drills into --------

    def test_bs_drill_down_matches_the_displayed_cumulative_balance(self):
        """A BS row is cumulative to date_to. The engine's default drill-down
        domain bounds on date_from, which would open a list totalling 700 under
        a row reading 1,100 — silently hiding the prior year."""
        wizard = self._bs()
        row = next(r for r in wizard._nc_compute_lines()
                   if r['account_id'] == self.receivable.id)
        domain = wizard._nc_move_line_domain(account=self.receivable)
        drilled = sum(self.env['account.move.line'].search(domain).mapped('balance'))
        self.assertAlmostEqual(drilled, 1100.0, places=2)
        self.assertAlmostEqual(row['current_amount'], drilled, places=2)

    def test_pl_drill_down_matches_the_displayed_period_movement(self):
        """P&L is a flow, so the engine's period-bounded default is already
        right — pin it so a future engine change cannot desync it."""
        wizard = self._pl()
        row = next(r for r in wizard._nc_compute_lines()
                   if r['account_id'] == self.revenue.id)
        domain = wizard._nc_move_line_domain(account=self.revenue)
        drilled = sum(self.env['account.move.line'].search(domain).mapped('balance'))
        # Revenue is credit-normal; the row negates it for presentation.
        self.assertAlmostEqual(row['current_amount'], -drilled, places=2)

    # ---- drill-down dispatch cannot be forged -----------------------------

    def test_report_lines_are_not_writable_at_all(self):
        """#318: nothing in the codebase writes to a report line — they are
        created and unlinked only. Granting perm_write left an RPC call_kw able
        to forge `report_model` on a row the caller owns, which is what made
        the drill-down dispatch forgeable in the first place. Removing the
        capability beats guarding what it enables.

        Must run as a REAL user: the test env is superuser, which bypasses ACLs
        entirely, so asserting this on `self.env` would pass no matter what the
        CSV said.
        """
        reader = self.env['res.users'].create({
            'name': 'Line Writer', 'login': 'nc_line_writer',
            'group_ids': [(6, 0, [self.env.ref('account.group_account_readonly').id,
                                  self.env.ref('base.group_user').id])],
        })
        wizard = self._bs().with_user(reader)
        action = wizard.action_view()
        line = self.env['ncollection.account.report.line'].with_user(
            reader).browse(action['domain'][0][2])[0]
        # Readable by its creator...
        self.assertTrue(line.label)
        # ...but not writable, by anyone, ever.
        with self.assertRaises(AccessError):
            line.write({'label': 'tampered'})

    def test_drill_down_rejects_a_forged_report_model(self):
        """`readonly=True` is only a form-view hint — an RPC write() can still
        set report_model. Unchecked, .browse().exists() applies neither ACL nor
        ir.rule, giving an existence oracle over the whole registry."""
        wizard = self._bs()
        action = wizard.action_view()
        line = self.env['ncollection.account.report.line'].browse(
            action['domain'][0][2])[0]
        for forged in ('res.users', 'ir.attachment', 'no.such.model'):
            with self.subTest(model=forged):
                # sudo() only to ARRANGE the forged value. #318 removed
                # perm_write so a real user can no longer reach this state at
                # all — but the dispatch guard must stand on its own, since an
                # ACL edit or a sudo'd code path would restore the write.
                # Testing them independently is the point: two controls, two
                # tests, neither propping the other up.
                line.sudo().write({'report_model': forged, 'report_res_id': 1})
                with self.assertRaises(UserError):
                    line.action_drill_down()

    def test_drill_down_still_works_for_a_legitimate_report_model(self):
        """The allow-list must not break the real path it guards."""
        wizard = self._bs()
        action = wizard.action_view()
        line = next(line for line in self.env[
            'ncollection.account.report.line'].browse(action['domain'][0][2])
            if line.account_id)
        drill = line.action_drill_down()
        self.assertEqual(drill['res_model'], 'account.move.line')

    # ---- export smoke ----------------------------------------------------

    def test_each_report_renders_under_its_own_report_action(self):
        """This module has a prior regression here: sharing one report_name
        makes Odoo resolve every wizard against the FIRST action's model."""
        seen = {}
        for wizard in (self._bs(), self._pl()):
            report = self.env.ref(wizard._nc_report_action_ref())
            self.assertEqual(report.model, wizard._name)
            seen[wizard._name] = report.report_name
        self.assertEqual(len(set(seen.values())), 2, seen)
        # ...and distinct from the two already shipped.
        for ref in ('action_report_trial_balance', 'action_report_general_ledger'):
            shipped = self.env.ref('ncollection_account_reports.%s' % ref)
            self.assertNotIn(shipped.report_name, set(seen.values()))

    def test_pdf_and_xlsx_render_for_both_reports(self):
        for wizard in (self._bs(comparison_type='previous_year'),
                       self._pl(comparison_type='previous_year')):
            with self.subTest(report=wizard._name):
                action = wizard.action_export_pdf()
                self.assertEqual(action['type'], 'ir.actions.report')
                # #250: the export streams from a controller and persists
                # nothing, so the workbook is checked at the source. The route
                # itself is exercised over HTTP in test_xlsx_stream.py.
                xlsx = wizard.action_export_xlsx()
                self.assertIn('/ncollection/account_reports/xlsx/', xlsx['url'])
                # A real workbook, not an empty/failed stream.
                self.assertTrue(
                    base64.b64decode(wizard._nc_build_xlsx()).startswith(b'PK'))

    def test_action_view_populates_the_comparison_columns(self):
        """Regression guard: the engine's action_view() must carry the F2-T03
        keys through to the line records, or the list renders all zeros."""
        wizard = self._pl(comparison_type='previous_year')
        action = wizard.action_view()
        lines = self.env['ncollection.account.report.line'].browse(
            action['domain'][0][2])
        net = lines.filtered(lambda line: line.label == 'NET PROFIT')
        self.assertAlmostEqual(net.current_amount, 700.0)
        self.assertAlmostEqual(net.previous_amount, 400.0)
        self.assertAlmostEqual(net.variance, 300.0)
