# -*- coding: utf-8 -*-
"""F2-T02: General Ledger + Trial Balance — running balance, FY-aware
opening/closing, and the acceptance that **TB closing reconciles with GL**.
"""
import base64
from datetime import date

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged


@tagged('post_install', '-at_install')
class TestGeneralLedgerTrialBalance(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.GL = cls.env['ncollection.account.report.general.ledger']
        cls.TB = cls.env['ncollection.account.report.trial.balance']
        cls.receivable = cls.company_data['default_account_receivable']   # balance sheet
        cls.revenue = cls.company_data['default_account_revenue']         # P&L
        cls.journal = cls.company_data['default_journal_misc']

        def entry(day, amount):
            move = cls.env['account.move'].create({
                'move_type': 'entry', 'journal_id': cls.journal.id, 'date': day,
                'line_ids': [
                    (0, 0, {'account_id': cls.receivable.id, 'debit': amount, 'credit': 0.0}),
                    (0, 0, {'account_id': cls.revenue.id, 'debit': 0.0, 'credit': amount}),
                ]})
            move.action_post()
            return move

        # Prior fiscal year (2025): Dr receivable 100 / Cr revenue 100.
        entry(date(2025, 12, 31), 100.0)
        # Current fiscal year (2026), inside the reporting period.
        entry(date(2026, 6, 15), 1000.0)
        cls.date_from = date(2026, 1, 1)
        cls.date_to = date(2026, 12, 31)

    def _tb(self):
        return self.TB.create({
            'date_from': self.date_from, 'date_to': self.date_to,
            'target_move': 'posted'})

    def _gl(self):
        return self.GL.create({
            'date_from': self.date_from, 'date_to': self.date_to,
            'target_move': 'posted'})

    # ---- Trial Balance: FY-aware opening/closing ------------------------

    def test_tb_opening_resets_for_pl_carries_for_bs(self):
        rows = {r['account_id']: r for r in self._tb()._nc_compute_lines()
                if r['account_id']}
        # balance-sheet (receivable): prior-year 100 CARRIES into opening
        self.assertAlmostEqual(rows[self.receivable.id]['opening_balance'], 100.0)
        # P&L (revenue): prior-year entry is EXCLUDED — opening resets to 0 at FY start
        self.assertAlmostEqual(rows[self.revenue.id]['opening_balance'], 0.0)

    def test_tb_closing_equals_opening_plus_movements(self):
        rows = {r['account_id']: r for r in self._tb()._nc_compute_lines()
                if r['account_id']}
        rec = rows[self.receivable.id]
        self.assertAlmostEqual(rec['closing_balance'],
                               rec['opening_balance'] + rec['debit'] - rec['credit'])
        self.assertAlmostEqual(rec['closing_balance'], 1100.0)   # 100 + 1000
        self.assertAlmostEqual(rows[self.revenue.id]['closing_balance'], -1000.0)

    def test_tb_total_row_debits_equal_credits(self):
        total = self._tb()._nc_compute_lines()[-1]
        self.assertFalse(total['account_id'])
        self.assertAlmostEqual(total['debit'], total['credit'])   # balanced books

    # ---- General Ledger: running balance + opening rows -----------------

    def test_gl_running_balance_starts_at_opening(self):
        rows = self._gl()._nc_gl_rows()
        # each account opens with an is_initial row carrying its opening balance
        rec_open = next(r for r in rows
                        if r['account_id'] == self.receivable.id and r['is_initial'])
        self.assertAlmostEqual(rec_open['running_balance'], 100.0)
        rev_open = next(r for r in rows
                        if r['account_id'] == self.revenue.id and r['is_initial'])
        self.assertAlmostEqual(rev_open['running_balance'], 0.0)

    def test_gl_running_balance_accumulates(self):
        rows = self._gl()._nc_gl_rows()
        rec_rows = [r for r in rows if r['account_id'] == self.receivable.id]
        # last row's running balance == opening + movements
        self.assertAlmostEqual(rec_rows[-1]['running_balance'], 1100.0)

    # ---- the acceptance: TB closing RECONCILES with GL ------------------

    def test_tb_closing_reconciles_with_gl_ending_balance(self):
        tb_rows = {r['account_id']: r for r in self._tb()._nc_compute_lines()
                   if r['account_id']}
        gl_rows = self._gl()._nc_gl_rows()
        # GL ending running balance per account = the last row for that account
        gl_ending = {}
        for row in gl_rows:
            if row['account_id']:
                gl_ending[row['account_id']] = row['running_balance']
        for account_id, tb in tb_rows.items():
            self.assertAlmostEqual(
                tb['closing_balance'], gl_ending[account_id],
                msg="TB closing must equal GL ending balance for account %s" % account_id)

    # ---- filters + drill-down + exports --------------------------------

    def test_gl_drill_down_is_account_scoped(self):
        gl = self._gl()
        gl.action_view()
        line = self.env['ncollection.account.report.gl.line'].search([
            ('account_id', '=', self.receivable.id), ('is_initial', '=', False)], limit=1)
        action = line.action_drill_down()
        self.assertEqual(action['res_model'], 'account.move.line')
        self.assertIn(('account_id', 'in', self.receivable.ids), action['domain'])

    def test_gl_journal_filter(self):
        bank = self.company_data['default_journal_bank']
        rows = self._gl().create({
            'date_from': self.date_from, 'date_to': self.date_to,
            'journal_ids': [(6, 0, [bank.id])]})._nc_gl_rows()
        # no bank-journal movement -> only opening rows (no period items)
        self.assertFalse([r for r in rows if not r['is_initial']])

    def test_exports_pdf_and_xlsx(self):
        for wizard in (self._tb(), self._gl()):
            report = self.env.ref(wizard._nc_report_action_ref())
            # Pass the report RECORD (not report_name): _get_report resolves a
            # record directly to its own model, so each wizard renders against
            # its own model — the shared-name ambiguity that browsed every wizard
            # against the reference model would otherwise raise MissingError.
            html, out_type = report._render_qweb_html(report, wizard.ids)
            self.assertEqual(out_type, 'html')
            self.assertTrue(html)
            self.assertEqual(base64.b64decode(wizard._nc_build_xlsx())[:2], b'PK')

    def test_gl_lines_are_private_per_user(self):
        self._gl().action_view()
        GLLine = self.env['ncollection.account.report.gl.line']
        self.assertTrue(GLLine.search([]))
        other = self.env['res.users'].create({
            'login': 'nc_gl_other', 'name': 'Other',
            'group_ids': [(6, 0, [self.env.ref('account.group_account_readonly').id])]})
        self.assertFalse(GLLine.with_user(other).search([]))

    # ---- opening classification: delegate to Odoo, don't hand-roll ------

    def test_opening_resets_for_other_expense_type(self):
        # 'expense_other' is an income-statement type Odoo resets each fiscal
        # year (include_initial_balance=False). A hand-rolled account_type
        # allowlist that omitted it wrongly carried its prior-year balance into
        # the opening — this proves we follow Odoo's own classification instead.
        other_exp = self.env['account.account'].create({
            'name': 'FX Loss', 'code': 'FXL001', 'account_type': 'expense_other',
            'company_ids': [(6, 0, self.env.company.ids)]})
        self.assertFalse(other_exp.include_initial_balance)   # Odoo: resets each FY
        move = self.env['account.move'].create({
            'move_type': 'entry', 'journal_id': self.journal.id,
            'date': date(2025, 12, 31),                       # prior fiscal year
            'line_ids': [
                (0, 0, {'account_id': other_exp.id, 'debit': 70.0, 'credit': 0.0}),
                (0, 0, {'account_id': self.receivable.id, 'debit': 0.0, 'credit': 70.0}),
            ]})
        move.action_post()
        opening = self._tb()._nc_opening_balances()
        # prior-year P&L activity must NOT carry into the current-year opening
        self.assertAlmostEqual(opening.get(other_exp.id, 0.0), 0.0)

    def test_opening_included_for_archived_account(self):
        # An ARCHIVED balance-sheet account still carries its prior balance —
        # the account partition must include it (active_test=False), or its
        # opening would be silently dropped to 0 while its row still shows.
        acct = self.env['account.account'].create({
            'name': 'Old Bank', 'code': 'OLDBK1', 'account_type': 'asset_cash',
            'company_ids': [(6, 0, self.env.company.ids)]})
        move = self.env['account.move'].create({
            'move_type': 'entry', 'journal_id': self.journal.id,
            'date': date(2025, 12, 31),               # prior fiscal year
            'line_ids': [
                (0, 0, {'account_id': acct.id, 'debit': 40.0, 'credit': 0.0}),
                (0, 0, {'account_id': self.revenue.id, 'debit': 0.0, 'credit': 40.0}),
            ]})
        move.action_post()
        acct.write({'active': False})                 # archived, still carries
        opening = self._tb()._nc_opening_balances()
        self.assertAlmostEqual(opening.get(acct.id, 0.0), 40.0)   # carried, not dropped

    def test_tb_opening_balances_to_zero_via_unaffected_earnings(self):
        # Prior fiscal years' net P&L rolls into the current-year-earnings
        # (equity_unaffected) account's opening — the affectation of results —
        # so the trial balance's OPENING column balances (Σ = 0), matching OCA
        # account_financial_report. Without it the opening would be off by the
        # prior P&L.
        cye = self.env['account.account'].search(
            [('account_type', '=', 'equity_unaffected')], limit=1)
        self.assertTrue(cye, "test chart has a current-year-earnings account")
        rows = [r for r in self._tb()._nc_compute_lines() if r['account_id']]
        self.assertAlmostEqual(sum(r['opening_balance'] for r in rows), 0.0)
        by_acc = {r['account_id']: r for r in rows}
        # setUpClass posted prior-year Cr revenue 100 → -100 net P&L rolled in
        self.assertAlmostEqual(by_acc[cye.id]['opening_balance'], -100.0)

    # ---- a report period must stay within one fiscal year --------------

    def test_report_rejects_multi_fiscal_year_period(self):
        wiz = self.TB.create({
            'date_from': date(2025, 6, 1), 'date_to': date(2026, 6, 1),
            'target_move': 'posted'})   # crosses the 2025→2026 FY boundary
        with self.assertRaises(UserError):
            wiz._nc_compute_lines()

    # ---- isolation: the wizard run + its export are private ------------

    def test_gl_run_and_export_are_private_per_user(self):
        # The drill-down pivot and the XLSX attachment (the F2-T01 IDOR class)
        # both hinge on another user being unable to read MY report run.
        gl = self._gl()
        gl.action_view()
        xlsx = gl.action_export_xlsx()
        att_id = int(xlsx['url'].split('/web/content/')[1].split('?')[0])
        other = self.env['res.users'].create({
            'login': 'nc_gl_reader', 'name': 'Reader',
            'group_ids': [(6, 0, [self.env.ref('account.group_account_readonly').id])]})
        # cannot read my GL wizard record (blocks the drill-down pivot)…
        with self.assertRaises(AccessError):
            gl.with_user(other).date_from
        # …nor download my GL export attachment
        with self.assertRaises(AccessError):
            self.env['ir.attachment'].browse(att_id).with_user(other).datas

    def test_gl_multiple_period_lines_ordered_by_date(self):
        # Several in-period entries for ONE account, inserted OUT of date order:
        # the one-pass grouping must still render them by date with a correct
        # cumulative running balance across all of them.
        for day, amount in [(20, 10.0), (5, 30.0), (12, 20.0)]:   # 20th, 5th, 12th
            mv = self.env['account.move'].create({
                'move_type': 'entry', 'journal_id': self.journal.id,
                'date': date(2026, 3, day),
                'line_ids': [
                    (0, 0, {'account_id': self.receivable.id, 'debit': amount, 'credit': 0.0}),
                    (0, 0, {'account_id': self.revenue.id, 'debit': 0.0, 'credit': amount}),
                ]})
            mv.action_post()
        gl = self._gl()
        rows = [r for r in gl._nc_gl_rows()
                if r['account_id'] == self.receivable.id and not r['is_initial']]
        dates = [r['date'] for r in rows]
        self.assertEqual(dates, sorted(dates))   # rendered in date order, not insertion
        running = gl._nc_opening_balances().get(self.receivable.id, 0.0)
        for r in rows:                            # running balance stays cumulative
            running += r['debit'] - r['credit']
            self.assertAlmostEqual(r['running_balance'], running)
