# -*- coding: utf-8 -*-
"""The "Exclude Zero Balances" option: one rule, every applicable report, every output.

THE RULE under test: a row is dropped only when EVERY amount column on it is zero. Activity is never hidden, totals
and hierarchy headers never change, and with the option off nothing changes at all. The viewer payload
(``nc_report_run``), the PDF payload and the XLSX are all built from the same ``_nc_compute_lines`` rows, so each
test asserts the three channels agree.
"""
import base64
import io
import zipfile
from datetime import date

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.tests import tagged

TB = 'ncollection.account.report.trial.balance'
GL = 'ncollection.account.report.general.ledger'
BS = 'ncollection.account.report.balance.sheet'
PL = 'ncollection.account.report.profit.loss'
PLEDGER = 'ncollection.account.report.partner.ledger'
STATEMENT = 'ncollection.account.report.statement'
AGED = 'ncollection.account.report.aged.partner'
NOT_APPLICABLE = ('ncollection.account.report.cash.flow', 'ncollection.account.report.uae.vat',
                  'ncollection.account.report.summary', 'ncollection.account.report.profitability')


@tagged('post_install', '-at_install')
class TestExcludeZeroBalances(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Service = cls.env['ncollection.report.service']
        cls.receivable = cls.company_data['default_account_receivable']
        cls.revenue = cls.company_data['default_account_revenue']
        cls.journal = cls.company_data['default_journal_misc']
        Account = cls.env['account.account']
        # opening nets to ZERO, never moves in the period -> the all-zero row the option exists to remove
        cls.dormant = Account.create({'code': 'ZXD100', 'name': 'Dormant Asset', 'account_type': 'asset_current'})
        # moves AND nets to zero inside the period -> real activity: must stay
        cls.churn = Account.create({'code': 'ZXC100', 'name': 'Churn Asset', 'account_type': 'asset_current'})
        # a revenue account that earns and reverses inside the period -> zero on the P&L
        cls.revenue_flat = Account.create({'code': 'ZXR100', 'name': 'Reversed Income', 'account_type': 'income'})
        cls.p_zero = cls.env['res.partner'].create({'name': 'Zero Partner'})
        cls.p_active = cls.env['res.partner'].create({'name': 'Active Partner'})
        cls.p_netted = cls.env['res.partner'].create({'name': 'Netted Same Bucket'})
        cls.p_spread = cls.env['res.partner'].create({'name': 'Spread Buckets'})

        def entry(day, debit_account, credit_account, amount, partner=False):
            cls.env['account.move'].create({
                'move_type': 'entry', 'journal_id': cls.journal.id, 'date': day, 'line_ids': [
                    (0, 0, {'account_id': debit_account.id, 'debit': amount, 'credit': 0.0,
                            'partner_id': partner.id if partner else False}),
                    (0, 0, {'account_id': credit_account.id, 'debit': 0.0, 'credit': amount,
                            'partner_id': partner.id if partner else False})]}).action_post()

        # prior year: dormant asset in and out; a partner debt incurred and cleared
        entry(date(2025, 12, 30), cls.dormant, cls.revenue, 100.0)
        entry(date(2025, 12, 31), cls.revenue, cls.dormant, 100.0)
        entry(date(2025, 12, 30), cls.receivable, cls.revenue, 100.0, cls.p_zero)
        entry(date(2025, 12, 31), cls.revenue, cls.receivable, 100.0, cls.p_zero)
        # the period: a real sale, churn that nets to zero, a reversed income account, two ageing partners
        entry(date(2026, 6, 15), cls.receivable, cls.revenue, 1000.0, cls.p_active)
        entry(date(2026, 3, 1), cls.churn, cls.revenue, 10.0)
        entry(date(2026, 3, 2), cls.revenue, cls.churn, 10.0)
        entry(date(2026, 4, 1), cls.receivable, cls.revenue_flat, 25.0)
        entry(date(2026, 4, 2), cls.revenue_flat, cls.receivable, 25.0)
        entry(date(2026, 6, 1), cls.receivable, cls.revenue, 50.0, cls.p_netted)
        entry(date(2026, 6, 1), cls.revenue, cls.receivable, 50.0, cls.p_netted)
        entry(date(2026, 12, 30), cls.receivable, cls.revenue, 80.0, cls.p_spread)
        entry(date(2026, 1, 2), cls.revenue, cls.receivable, 80.0, cls.p_spread)
        cls.filters = {'date_from': date(2026, 1, 1), 'date_to': date(2026, 12, 31), 'target_move': 'posted'}

    # ------------------------------------------------------------------ helpers
    def _wizard(self, model, exclude=False, **extra):
        return self.env[model].create(dict(self.filters, exclude_zero=exclude, **extra))

    def _accounts(self, rows):
        return {r['account_id'] for r in rows if r.get('account_id')}

    def _partners(self, rows):
        return {r['partner_id'] for r in rows if r.get('partner_id')}

    def _xlsx_text(self, wizard):
        book = zipfile.ZipFile(io.BytesIO(base64.b64decode(wizard._nc_build_xlsx())))
        return book.read('xl/sharedStrings.xml').decode()

    # ------------------------------------------------------------------ the field
    def test_the_option_exists_on_applicable_reports_only_and_defaults_to_off(self):
        for model in (TB, GL, BS, PL, PLEDGER, STATEMENT, AGED, 'ncollection.account.report.revenue',
                      'ncollection.account.report.expense', 'ncollection.account.report.cost.center',
                      'ncollection.account.report.profit.center', 'ncollection.account.report.reference'):
            field = self.env[model]._fields.get('exclude_zero')
            self.assertTrue(field, model)
            self.assertFalse(field.default(self.env[model]) if callable(field.default) else field.default, model)
        for model in NOT_APPLICABLE:
            if model in self.env:
                self.assertNotIn('exclude_zero', self.env[model]._fields, model)

    def test_the_viewer_lists_it_as_a_filter(self):
        data = self.Service.nc_report_run(TB, dict(self.filters, exclude_zero=False))
        names = {f['name'] for f in data['filters']}
        self.assertIn('exclude_zero', names)
        label = next(f['label'] for f in data['filters'] if f['name'] == 'exclude_zero')
        self.assertEqual(label, 'Exclude Zero Balances')

    # ------------------------------------------------------------------ Trial Balance
    def test_trial_balance_drops_only_the_all_zero_row_and_keeps_totals(self):
        off = self._wizard(TB)._nc_compute_lines()
        on = self._wizard(TB, True)._nc_compute_lines()
        self.assertIn(self.dormant.id, self._accounts(off))                       # option OFF: nothing changes
        self.assertNotIn(self.dormant.id, self._accounts(on))                     # opening nets to 0, no movement
        self.assertIn(self.churn.id, self._accounts(on))                          # moved and netted to 0: activity
        self.assertEqual(self._accounts(off) - self._accounts(on), {self.dormant.id})
        total = lambda rows: next(r for r in rows if r['level'] == 0)             # noqa: E731
        for key in ('opening_balance', 'debit', 'credit', 'closing_balance'):
            self.assertAlmostEqual(total(off)[key], total(on)[key], msg=key)

    def test_trial_balance_off_is_byte_for_byte_the_old_behaviour(self):
        default = self.env[TB].create(self.filters)._nc_compute_lines()
        explicit = self._wizard(TB, False)._nc_compute_lines()
        self.assertEqual(default, explicit)

    # ------------------------------------------------------------------ ledgers
    def test_general_ledger_drops_the_account_with_no_opening_and_no_movement(self):
        off, on = self._wizard(GL)._nc_gl_rows(), self._wizard(GL, True)._nc_gl_rows()
        self.assertIn(self.dormant.id, self._accounts(off))
        self.assertNotIn(self.dormant.id, self._accounts(on))
        self.assertIn(self.churn.id, self._accounts(on))
        # every kept account keeps its opening row AND its items, with identical running balances
        kept = [r for r in off if r['account_id'] != self.dormant.id]
        self.assertEqual(kept, on)

    def test_partner_ledger_and_statement_drop_the_partner_with_nothing_to_show(self):
        for model in (PLEDGER, STATEMENT):
            off, on = self._wizard(model)._nc_partner_rows(), self._wizard(model, True)._nc_partner_rows()
            self.assertIn(self.p_zero.id, self._partners(off), model)
            self.assertNotIn(self.p_zero.id, self._partners(on), model)
            self.assertIn(self.p_active.id, self._partners(on), model)
            self.assertEqual([r for r in off if r['partner_id'] != self.p_zero.id], on, model)

    def test_aged_receivable_drops_only_partners_whose_every_bucket_is_zero(self):
        off = self._wizard(AGED)._nc_aged_rows()
        on = self._wizard(AGED, True)._nc_aged_rows()
        self.assertIn(self.p_netted.id, self._partners(off))
        self.assertNotIn(self.p_netted.id, self._partners(on))
        # +80 in one bucket and -80 in another nets to a zero TOTAL but is real ageing information: kept
        spread = next(r for r in on if r['partner_id'] == self.p_spread.id)
        self.assertAlmostEqual(spread['total'], 0.0)
        self.assertTrue(any(abs(spread[k]) > 0.005 for k in spread if k.startswith('bucket_')))
        # Zero Partner (a prior-year debt and its repayment, both unreconciled and both long overdue) nets to zero in
        # one bucket too, so it goes with the other: exactly the partners whose every bucket is zero, no more.
        self.assertEqual({r['partner_id'] for r in off} - {r['partner_id'] for r in on},
                         {self.p_netted.id, self.p_zero.id})
        self.assertIn(self.p_active.id, self._partners(on))

    # ------------------------------------------------------------------ statements
    def test_balance_sheet_and_profit_loss_keep_structure_and_totals(self):
        for model, gone in ((BS, self.dormant), (PL, self.revenue_flat)):
            off = self._wizard(model, comparison_type='none')._nc_compute_lines()
            on = self._wizard(model, True, comparison_type='none')._nc_compute_lines()
            self.assertIn(gone.id, self._accounts(off), model)
            self.assertNotIn(gone.id, self._accounts(on), model)
            for level in (0, 1):                                    # headers, buckets, subtotals untouched
                self.assertEqual([r for r in off if r['level'] == level], [r for r in on if r['level'] == level],
                                 '%s level %s' % (model, level))
            # nothing that carries an amount was dropped
            dropped = [r for r in off if r not in on]
            self.assertTrue(dropped, model)
            for row in dropped:
                self.assertAlmostEqual(row['current_amount'], 0.0)
                self.assertAlmostEqual(row['previous_amount'], 0.0)

    def test_the_balance_sheet_still_balances_with_the_option_on(self):
        wizard = self._wizard(BS, True, comparison_type='none')
        assets, liabilities_equity = wizard._nc_totals()
        reference = self._wizard(BS, False, comparison_type='none')._nc_totals()
        self.assertAlmostEqual(assets, reference[0])
        self.assertAlmostEqual(liabilities_equity, reference[1])

    # ------------------------------------------------------------------ the three outputs agree
    def test_viewer_pdf_and_xlsx_drop_the_same_rows(self):
        gone = {TB: 'Dormant Asset', GL: 'Dormant Asset', BS: 'Dormant Asset', PL: 'Reversed Income',
                PLEDGER: 'Zero Partner', AGED: 'Netted Same Bucket'}
        for model, name in gone.items():
            extra = {'comparison_type': 'none'} if model in (BS, PL) else {}
            seen = {}
            for exclude in (False, True):
                data = self.Service.nc_report_run(model, dict(self.filters, exclude_zero=exclude, **extra))
                wizard = self.env[model].browse(data['wizard']['id'])
                payload_rows = wizard._nc_export_payload()['rows']
                self.assertEqual(len(data['rows']), len(payload_rows), '%s viewer vs print payload' % model)
                seen[exclude] = (len(data['rows']), name in str(data['rows']) + str(payload_rows),
                                 name in self._xlsx_text(wizard))
            self.assertGreater(seen[False][0], seen[True][0], model)           # rows were removed ...
            self.assertEqual(seen[False][1:], (True, True), model)             # ... present with the option off
            self.assertEqual(seen[True][1:], (False, False), model)            # ... gone from payload AND workbook

    def test_the_printed_copy_states_the_option(self):
        on = self._wizard(TB, True)._nc_export_payload()
        off = self._wizard(TB, False)._nc_export_payload()
        self.assertIn(('Exclude Zero Balances', 'Yes'), on['filters'])
        self.assertNotIn('Exclude Zero Balances', [label for label, _t in off['filters']])

    def test_arabic_labels_and_rtl_output(self):
        self.env['res.lang']._activate_lang('ar_001')
        self.env['ir.module.module']._load_module_terms(
            ['ncollection_account_reports'], ['ar_001'], overwrite=True)
        wizard = self.env[TB].with_context(lang='ar_001').create(dict(self.filters, exclude_zero=True))
        payload = wizard._nc_export_payload()
        self.assertEqual(payload['direction'], 'rtl')
        self.assertIn(('استبعاد الأرصدة الصفرية', 'نعم'), payload['filters'])
        self.assertNotIn(self.dormant.display_name, str(payload['rows']))
