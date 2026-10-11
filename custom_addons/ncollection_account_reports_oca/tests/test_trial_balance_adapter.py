# -*- coding: utf-8 -*-
"""The OCA-backed Trial Balance must agree with the native one, and stay private.

The pilot's acceptance criterion is CORRECTNESS FIRST: on the same data and the
same filters the two engines must produce the same figures for every account.
Everything the viewer, PDF and XLSX do afterwards is shared NCollection code, so
these tests concentrate on the adapter's own seams: the mapped numbers, the
hierarchy/Total rows, the source switch and its fallbacks, that OCA's wizard row
does not linger, and that nobody gets figures they are not allowed to see.
"""
import base64
import io
import re
import zipfile
from datetime import date

from odoo.addons.account.tests.common import (
    AccountTestInvoicingCommon, AccountTestInvoicingHttpCommon,
)
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

NATIVE = 'ncollection.account.report.trial.balance'
OCA = 'ncollection.account.report.tb.oca'
PARAM = 'ncollection_account_reports.source.trial_balance'


def _figures(rows, nonzero=False):
    out = {r['account_id']: tuple(round(r[k], 2) for k in
                                  ('opening_balance', 'debit', 'credit', 'closing_balance'))
           for r in rows if r['account_id']}
    return {k: v for k, v in out.items() if any(v)} if nonzero else out


class _Data(AccountTestInvoicingCommon):

    @classmethod
    def _build(cls):
        cls.receivable = cls.company_data['default_account_receivable']
        cls.payable = cls.company_data['default_account_payable']
        cls.revenue = cls.company_data['default_account_revenue']
        cls.expense = cls.company_data['default_account_expense']
        cls.journal = cls.company_data['default_journal_misc']
        cls.journal2 = cls.env['account.journal'].create(
            {'name': 'Second', 'code': 'SEC', 'type': 'general',
             'company_id': cls.env.company.id})
        cls.partner = cls.partner_a

        def entry(day, a1, a2, amount, journal=None, post=True, partner=None):
            move = cls.env['account.move'].create({
                'move_type': 'entry', 'journal_id': (journal or cls.journal).id, 'date': day,
                'line_ids': [
                    (0, 0, {'account_id': a1.id, 'debit': amount, 'credit': 0.0,
                            'partner_id': (partner or cls.env['res.partner']).id}),
                    (0, 0, {'account_id': a2.id, 'debit': 0.0, 'credit': amount,
                            'partner_id': (partner or cls.env['res.partner']).id})]})
            if post:
                move.action_post()
            return move

        # prior fiscal year: balance-sheet carries, P&L must roll into earnings
        entry(date(2025, 11, 5), cls.receivable, cls.revenue, 1000.0, partner=cls.partner)
        entry(date(2025, 12, 20), cls.expense, cls.payable, 150.0)
        # current year, inside the period
        entry(date(2026, 2, 10), cls.receivable, cls.revenue, 400.0, partner=cls.partner)
        entry(date(2026, 5, 15), cls.expense, cls.payable, 75.5, journal=cls.journal2)
        entry(date(2026, 9, 1), cls.receivable, cls.revenue, 25.25, journal=cls.journal2)
        # a draft entry: only visible with "all entries"
        entry(date(2026, 6, 1), cls.expense, cls.payable, 999.0, post=False)
        cls.base = {'date_from': date(2026, 1, 1), 'date_to': date(2026, 12, 31)}

    def _run(self, model, **extra):
        vals = dict(self.base, **extra)
        if model == OCA:
            vals.setdefault('hide_zero', False)
        return self.env[model].create(vals)


@tagged('post_install', '-at_install')
class TestTrialBalanceAdapter(_Data):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._build()
        cls.Service = cls.env['ncollection.report.service']

    # ------------------------------------------------------- correctness
    def _same(self, **extra):
        native = _figures(self._run(NATIVE, **extra)._nc_compute_lines())
        oca = _figures(self._run(OCA, **extra)._nc_compute_lines())
        for account in set(native) | set(oca):
            self.assertEqual(
                native.get(account, (0.0, 0.0, 0.0, 0.0)), oca.get(account, (0.0, 0.0, 0.0, 0.0)),
                'account %s differs for %s' % (self.env['account.account'].browse(account).code, extra))
        return native

    def test_the_two_engines_agree_for_the_default_filters(self):
        figures = self._same()
        self.assertTrue(figures)
        # a real opening balance is exercised (the prior fiscal year carries over)
        self.assertEqual(figures[self.receivable.id][0], 1000.0)

    def test_the_two_engines_agree_under_every_filter(self):
        self._same(date_from=date(2026, 3, 1), date_to=date(2026, 6, 30))
        self._same(target_move='all')
        self._same(journal_ids=[(6, 0, self.journal2.ids)])
        self._same(partner_ids=[(6, 0, self.partner.ids)])
        self._same(account_ids=[(6, 0, (self.revenue + self.expense).ids)])
        self._same(date_from=date(2025, 1, 1), date_to=date(2025, 12, 31))

    def test_all_entries_includes_the_draft_and_posted_does_not(self):
        posted = _figures(self._run(OCA)._nc_compute_lines())
        everything = _figures(self._run(OCA, target_move='all')._nc_compute_lines())
        self.assertNotEqual(posted[self.expense.id], everything[self.expense.id])
        self.assertEqual(everything[self.expense.id][1] - posted[self.expense.id][1], 999.0)

    def test_totals_equal_the_native_totals_and_balance(self):
        n = self._run(NATIVE)._nc_compute_lines()[-1]
        o = self._run(OCA)._nc_compute_lines()[-1]
        for key in ('opening_balance', 'debit', 'credit', 'closing_balance'):
            self.assertAlmostEqual(n[key], o[key], places=2, msg=key)
        self.assertAlmostEqual(o['debit'], o['credit'], places=2)
        self.assertAlmostEqual(o['closing_balance'], 0.0, places=2)
        self.assertEqual(o['level'], 0)
        self.assertFalse(o['account_id'])

    # ---------------------------------------------------------- contract
    def test_columns_are_identical_to_the_native_report(self):
        self.assertEqual(self._run(OCA)._nc_columns(), self._run(NATIVE)._nc_columns())

    def test_rows_carry_only_real_account_ids(self):
        rows = self._run(OCA)._nc_compute_lines()
        ids = [r['account_id'] for r in rows if r['account_id']]
        self.assertTrue(ids)
        self.assertEqual(set(ids), set(self.env['account.account'].browse(ids).exists().ids))

    def test_the_hierarchy_has_group_rows_without_a_link_above_their_accounts(self):
        prefix = self.revenue.code[:1]
        self.env['account.group'].create({
            'name': 'Pilot group', 'code_prefix_start': prefix, 'code_prefix_end': prefix})
        rows = self._run(OCA, show_hierarchy=True)._nc_compute_lines()
        groups = [r for r in rows if not r['account_id'] and r['level'] > 0]
        accounts = [r for r in rows if r['account_id']]
        self.assertTrue(groups, 'show_hierarchy produced no group rows')
        self.assertTrue(accounts)
        self.assertLess(min(g['level'] for g in groups), max(a['level'] for a in accounts))
        # groups are subtotals: accounts are not double counted in the Total row
        flat = self._run(OCA)._nc_compute_lines()[-1]
        self.assertAlmostEqual(rows[-1]['debit'], flat['debit'], places=2)

    def test_the_single_fiscal_year_policy_applies_to_both_engines(self):
        for model in (NATIVE, OCA):
            wizard = self._run(model, date_from=date(2025, 6, 1), date_to=date(2026, 6, 1))
            with self.assertRaises(UserError):
                wizard._nc_compute_lines()

    def test_oca_wizard_rows_do_not_linger(self):
        before = self.env['trial.balance.report.wizard'].sudo().search_count([])
        self._run(OCA)._nc_compute_lines()
        self.assertEqual(self.env['trial.balance.report.wizard'].sudo().search_count([]), before)

    # ---------------------------------------------------- the source switch
    def test_the_default_source_is_native(self):
        self.env['ir.config_parameter'].sudo().set_param(PARAM, 'native')
        data = self.Service.nc_report_run(NATIVE, dict(self.base))
        self.assertEqual(data['source'], 'native')
        self.assertEqual(data['wizard']['model'], NATIVE)

    def test_selecting_oca_runs_the_adapter_and_says_so(self):
        self.env['ir.config_parameter'].sudo().set_param(PARAM, 'oca')
        data = self.Service.nc_report_run(NATIVE, dict(self.base))
        self.assertEqual(data['source'], 'oca')
        self.assertEqual(data['wizard']['model'], OCA)

    def test_an_unknown_switch_value_falls_back_to_native(self):
        self.env['ir.config_parameter'].sudo().set_param(PARAM, 'bogus')
        self.assertEqual(self.Service.nc_report_run(NATIVE, dict(self.base))['source'], 'native')

    def test_both_sources_return_the_same_viewer_figures(self):
        self.env['ir.config_parameter'].sudo().set_param(PARAM, 'native')
        native = self.Service.nc_report_run(NATIVE, dict(self.base, partner_ids=[]))
        self.env['ir.config_parameter'].sudo().set_param(PARAM, 'oca')
        oca = self.Service.nc_report_run(NATIVE, dict(self.base, partner_ids=[], hide_zero=False))
        self.assertEqual(_figures(native['rows'], True), _figures(oca['rows'], True))
        self.assertEqual([c['key'] for c in native['columns']], [c['key'] for c in oca['columns']])

    def test_export_and_detail_use_the_wizard_the_run_actually_created(self):
        self.env['ir.config_parameter'].sudo().set_param(PARAM, 'oca')
        data = self.Service.nc_report_run(NATIVE, dict(self.base))
        action = self.Service.nc_report_export(data['wizard']['model'], data['wizard']['id'], 'pdf')
        self.assertEqual(action['type'], 'ir.actions.report')
        xlsx = self.Service.nc_report_export(data['wizard']['model'], data['wizard']['id'], 'xlsx')
        self.assertEqual(xlsx['type'], 'ir.actions.act_url')

    def test_the_oca_filters_are_exposed_to_the_viewer(self):
        data = self.Service.nc_report_run(OCA, dict(self.base))
        names = {f['name'] for f in data['filters']}
        self.assertTrue({'hide_zero', 'show_hierarchy', 'journal_ids', 'partner_ids'} <= names)

    # ---------------------------------------------------- print channels
    def _sheet(self, wizard):
        book = zipfile.ZipFile(io.BytesIO(base64.b64decode(wizard._nc_build_xlsx())))
        return book.read('xl/worksheets/sheet1.xml').decode(), book

    def test_the_xlsx_has_real_numbers_and_a_frozen_header(self):
        sheet, _ = self._sheet(self._run(OCA))
        self.assertIn('state="frozen"', sheet)
        self.assertRegex(sheet, r'<c r="[B-E]\d+" s="\d+"><v>1000(\.0)?</v></c>')

    def test_the_xlsx_is_right_to_left_for_arabic_only(self):
        self.env['res.lang']._activate_lang('ar_001')
        arabic, _ = self._sheet(self._run(OCA).with_context(lang='ar_001'))
        english, _ = self._sheet(self._run(OCA))
        self.assertIn('rightToLeft="1"', arabic)
        self.assertNotIn('rightToLeft="1"', english)

    def test_the_print_payload_is_identical_in_shape_to_the_native_one(self):
        n = self._run(NATIVE)._nc_export_payload()
        o = self._run(OCA)._nc_export_payload()
        self.assertEqual([c['key'] for c in n['columns']], [c['key'] for c in o['columns']])
        self.assertEqual(n['title'], o['title'])
        self.assertEqual(n['direction'], o['direction'])

    def test_the_html_print_renders_and_states_the_figures(self):
        wizard = self._run(OCA)
        action = self.env.ref(wizard._nc_report_action_ref())
        html = action._render_qweb_html(action.report_name, [wizard.id])[0]
        text = html.decode() if isinstance(html, bytes) else html
        self.assertIn('1,000.00', text)
        self.assertIn('Trial Balance', text)

    # ------------------------------------------------------------ security
    def _user(self, login, *group_xmlids):
        groups = [self.env.ref('base.group_user').id] + [self.env.ref(x).id for x in group_xmlids]
        return self.env['res.users'].with_context(no_reset_password=True).create({
            'name': login, 'login': login, 'group_ids': [(6, 0, groups)]})

    def test_a_user_without_accounting_rights_cannot_run_it(self):
        clerk = self._user('tb_oca_clerk')
        with self.assertRaises(AccessError):
            self.env[OCA].with_user(clerk).create(dict(self.base))
        with self.assertRaises(AccessError):
            self.Service.with_user(clerk).nc_report_run(NATIVE, dict(self.base))

    def test_a_run_is_private_to_the_user_who_made_it(self):
        accountant = self._user('tb_oca_acc1', 'account.group_account_user')
        other = self._user('tb_oca_acc2', 'account.group_account_user')
        run = self.env[OCA].with_user(accountant).create(dict(self.base))
        self.assertTrue(self.env[OCA].with_user(accountant).search([('id', '=', run.id)]))
        self.assertFalse(self.env[OCA].with_user(other).search([('id', '=', run.id)]))

    def test_an_accounting_reader_gets_the_same_figures_as_the_owner_of_the_books(self):
        reader = self._user('tb_oca_reader', 'account.group_account_readonly')
        mine = _figures(self.env[OCA].with_user(reader).create(
            dict(self.base, hide_zero=False))._nc_compute_lines())
        full = _figures(self._run(OCA)._nc_compute_lines())
        self.assertEqual(mine, full)

    def test_it_never_returns_another_companys_figures(self):
        other_company = self.env['res.company'].create({'name': 'Other Co'})
        accountant = self._user('tb_oca_single', 'account.group_account_user')
        accountant.company_ids = [(6, 0, self.env.company.ids)]
        accountant.company_id = self.env.company
        # the engine refuses to even CREATE a wizard for a company the user is not in...
        with self.assertRaises(AccessError):
            self.env[OCA].with_user(accountant).create(dict(self.base, company_id=other_company.id))
        # ...and, defence in depth, a wizard that exists anyway still computes nothing for that user
        wizard = self.env[OCA].sudo().create(dict(self.base, company_id=other_company.id))
        with self.assertRaises(AccessError):
            wizard.with_user(accountant)._nc_compute_lines()
        with self.assertRaises(AccessError):
            self.Service.with_user(accountant).nc_report_run(
                NATIVE, dict(self.base, company_id=other_company.id))
        # and nothing was created in the foreign company as a side effect
        self.assertFalse(self.env['account.account'].sudo().search_count(
            [('company_ids', 'in', other_company.ids)]))

    def test_the_adapter_never_uses_sudo_for_the_figures(self):
        """OCA's engine runs as the caller; this is a static guard on the adapter."""
        import inspect
        from odoo.addons.ncollection_account_reports_oca.models import trial_balance_oca
        self.assertNotRegex(inspect.getsource(trial_balance_oca), r'\.sudo\(\)\.')

    # --------------------------------------------------------------- sanity
    def test_a_run_over_a_few_hundred_lines_is_fast(self):
        import time
        moves = [{'move_type': 'entry', 'journal_id': self.journal.id,
                  'date': date(2026, 1, 1 + i % 27),
                  'line_ids': [(0, 0, {'account_id': self.receivable.id, 'debit': 10.0 + i, 'credit': 0.0}),
                               (0, 0, {'account_id': self.revenue.id, 'debit': 0.0, 'credit': 10.0 + i})]}
                 for i in range(150)]
        for move in self.env['account.move'].create(moves):
            move.action_post()
        started = time.time()
        rows = self._run(OCA)._nc_compute_lines()
        self.assertLess(time.time() - started, 5.0)
        self.assertGreater(len(rows), 2)


@tagged('post_install', '-at_install')
class TestTrialBalanceAdapterPdf(AccountTestInvoicingHttpCommon):
    """A real wkhtmltopdf run needs a live HTTP server (see test_report_print)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _Data._build.__func__(cls)

    def setUp(self):
        super().setUp()
        self.env['ir.config_parameter'].sudo().set_param('web.base.url', self.base_url())

    def _pdf(self, lang=None):
        wizard = self.env[OCA].create(dict(self.base, hide_zero=False))
        action = self.env.ref(wizard._nc_report_action_ref()).with_context(
            force_report_rendering=True, **({'lang': lang} if lang else {}))
        return action._render_qweb_pdf(action.report_name, [wizard.id])[0]

    def test_the_pdf_renders_in_english_and_arabic(self):
        self.env['res.lang']._activate_lang('ar_001')
        for lang in (None, 'ar_001'):
            self.assertTrue(self._pdf(lang).startswith(b'%PDF'), lang)

    def test_a_long_report_paginates(self):
        for i in range(120):
            account = self.env['account.account'].create({
                'name': 'Pilot %03d' % i, 'code': 'PL%05d' % i, 'account_type': 'expense'})
            move = self.env['account.move'].create({
                'move_type': 'entry', 'journal_id': self.journal.id, 'date': date(2026, 3, 1),
                'line_ids': [(0, 0, {'account_id': account.id, 'debit': 5.0 + i, 'credit': 0.0}),
                             (0, 0, {'account_id': self.revenue.id, 'debit': 0.0, 'credit': 5.0 + i})]})
            move.action_post()
        pages = len(re.findall(rb'/Type\s*/Page[^s]', self._pdf()))
        self.assertGreater(pages, 2)
