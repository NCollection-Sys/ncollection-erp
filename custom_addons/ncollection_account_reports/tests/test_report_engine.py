# -*- coding: utf-8 -*-
"""F2-T01: the report engine foundation — filters, compute, native list,
drill-down, PDF + XLSX, and the < 2s perf target. Uses Odoo's accounting test
base (a company with a chart of accounts + journals)."""
import base64
import time

from odoo import fields
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.tests import tagged


@tagged('post_install', '-at_install')
class TestReportEngine(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Report = cls.env['ncollection.account.report.reference']
        cls.receivable = cls.company_data['default_account_receivable']
        cls.revenue = cls.company_data['default_account_revenue']
        cls.misc_journal = cls.company_data['default_journal_misc']
        # A posted, balanced journal entry: Dr receivable 1000 / Cr revenue 1000.
        cls.posted = cls.env['account.move'].create({
            'move_type': 'entry',
            'journal_id': cls.misc_journal.id,
            'date': fields.Date.today(),
            'line_ids': [
                (0, 0, {'account_id': cls.receivable.id, 'debit': 1000.0, 'credit': 0.0}),
                (0, 0, {'account_id': cls.revenue.id, 'debit': 0.0, 'credit': 1000.0}),
            ],
        })
        cls.posted.action_post()

    def _wizard(self, **kw):
        vals = {
            'date_from': fields.Date.today().replace(month=1, day=1),
            'date_to': fields.Date.today(),
            'target_move': 'posted',
        }
        vals.update(kw)
        return self.Report.create(vals)

    # ---- compute + filters ----------------------------------------------

    def test_reference_report_computes_per_account_balances(self):
        lines = self._wizard()._nc_compute_lines()
        by_account = {row['account_id']: row for row in lines if row['account_id']}
        self.assertIn(self.receivable.id, by_account)
        self.assertIn(self.revenue.id, by_account)
        self.assertAlmostEqual(by_account[self.receivable.id]['debit'], 1000.0)
        self.assertAlmostEqual(by_account[self.revenue.id]['credit'], 1000.0)
        # a balanced total row closes the report
        total = lines[-1]
        self.assertFalse(total['account_id'])
        self.assertAlmostEqual(total['debit'], total['credit'])

    def test_target_move_filter_excludes_draft(self):
        draft = self.env['account.move'].create({
            'move_type': 'entry', 'journal_id': self.misc_journal.id,
            'date': fields.Date.today(),
            'line_ids': [
                (0, 0, {'account_id': self.receivable.id, 'debit': 500.0}),
                (0, 0, {'account_id': self.revenue.id, 'credit': 500.0})]})
        self.assertEqual(draft.state, 'draft')
        posted_total = self._wizard(target_move='posted')._nc_compute_lines()[-1]['debit']
        all_total = self._wizard(target_move='all')._nc_compute_lines()[-1]['debit']
        self.assertAlmostEqual(posted_total, 1000.0)   # draft excluded
        self.assertAlmostEqual(all_total, 1500.0)      # draft included

    def test_account_filter_scopes_the_report(self):
        lines = self._wizard(account_ids=[(6, 0, [self.receivable.id])])._nc_compute_lines()
        accounts = {row['account_id'] for row in lines if row['account_id']}
        self.assertEqual(accounts, {self.receivable.id})

    def test_report_lines_are_private_per_user(self):
        # A user's report run is private: another accounting user must not see
        # (nor be able to clear) it — the create_uid ir.rule (isolation fix).
        self._wizard().action_view()
        Line = self.env['ncollection.account.report.line']
        self.assertTrue(Line.search([]))
        other = self.env['res.users'].create({
            'login': 'nc_rpt_other', 'name': 'Other Accountant',
            'group_ids': [(6, 0, [self.env.ref('account.group_account_readonly').id])]})
        self.assertFalse(Line.with_user(other).search([]))

    # ---- on-screen list + drill-down ------------------------------------

    def test_action_view_creates_line_records(self):
        action = self._wizard().action_view()
        self.assertEqual(action['res_model'], 'ncollection.account.report.line')
        line_ids = action['domain'][0][2]
        self.assertTrue(line_ids)

    def test_drill_down_opens_scoped_journal_items(self):
        wizard = self._wizard()
        wizard.action_view()
        line = self.env['ncollection.account.report.line'].search([
            ('report_model', '=', wizard._name),
            ('account_id', '=', self.receivable.id)], limit=1)
        action = line.action_drill_down()
        self.assertEqual(action['res_model'], 'account.move.line')
        self.assertIn(('account_id', 'in', self.receivable.ids), action['domain'])
        moves = self.env['account.move.line'].search(action['domain'])
        self.assertTrue(moves)
        self.assertTrue(all(ml.account_id == self.receivable for ml in moves))

    # ---- exports --------------------------------------------------------

    def test_pdf_template_renders(self):
        wizard = self._wizard()
        report = self.env.ref('ncollection_account_reports.action_report_financial')
        html, out_type = report._render_qweb_html(
            'ncollection_account_reports.report_financial', wizard.ids)
        self.assertEqual(out_type, 'html')
        self.assertIn(b'Account Balances', html)

    def test_xlsx_export_produces_a_workbook(self):
        data = self._wizard()._nc_build_xlsx()
        self.assertTrue(data)
        self.assertEqual(base64.b64decode(data)[:2], b'PK')  # xlsx == zip container

    # ---- performance (acceptance: < 2s) ---------------------------------

    def test_row_order_is_consistent_total_last(self):
        # id order == compute order == PDF/XLSX order: the Total row renders LAST,
        # never sorted to the top by level (the render-consistency fix).
        action = self._wizard().action_view()
        lines = self.env['ncollection.account.report.line'].browse(
            action['domain'][0][2]).sorted('id')
        self.assertEqual(lines[-1].level, 0)
        self.assertEqual(lines[-1].label, 'Total')
        self.assertTrue(all(line.level == 1 for line in lines[:-1]))

    def test_action_view_replaces_previous_run(self):
        wizard = self._wizard()
        wizard.action_view()
        after_first = self.env['ncollection.account.report.line'].search_count([])
        wizard.action_view()
        after_second = self.env['ncollection.account.report.line'].search_count([])
        self.assertEqual(after_first, after_second)  # replaced, not accumulated

    def test_journal_filter_scopes_the_report(self):
        bank = self.company_data['default_journal_bank']
        lines = self._wizard(journal_ids=[(6, 0, [bank.id])])._nc_compute_lines()
        self.assertFalse([row for row in lines if row['account_id']])  # entries are in misc

    def test_export_action_wrappers(self):
        wizard = self._wizard()
        xlsx = wizard.action_export_xlsx()
        self.assertEqual(xlsx['type'], 'ir.actions.act_url')
        self.assertIn('/web/content/', xlsx['url'])
        pdf = wizard.action_export_pdf()
        self.assertEqual(pdf['type'], 'ir.actions.report')

    def test_reference_report_under_2s(self):
        # A wider dataset across several accounts; time the WHOLE action_view
        # (aggregate compute + batched materialize), not just the compute.
        extra_accounts = self.env['account.account'].create([{
            'name': 'Perf %s' % i, 'code': 'PERF%03d' % i, 'account_type': 'expense',
        } for i in range(20)])
        move_lines = [(0, 0, {'account_id': self.revenue.id, 'credit': 20 * 10.0})]
        for acc in extra_accounts:
            move_lines.append((0, 0, {'account_id': acc.id, 'debit': 10.0}))
        self.env['account.move'].create({
            'move_type': 'entry', 'journal_id': self.misc_journal.id,
            'date': fields.Date.today(), 'line_ids': move_lines}).action_post()
        started = time.time()
        action = self._wizard().action_view()
        self.assertLess(time.time() - started, 2.0)
        self.assertTrue(action['domain'][0][2])
