# -*- coding: utf-8 -*-
"""XBRL export (final review #11): the facts ARE the report's figures, the file is a real XBRL 2.1 instance against
the IFRS 2025 entry point, and the route is no new way in. Independent schema / taxonomy validation is Arelle's
(docs/markdown/XBRL_EXPORT.md); these tests pin what the module decides."""
import datetime

from lxml import etree

from odoo import Command
from odoo.tests import HttpCase, tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.addons.ncollection_account_reports.models.xbrl_export import NS, TAXONOMY_ENTRY_POINT

_ROUTE = '/ncollection/account_reports/xbrl/%s/%s'


@tagged('post_install', '-at_install')
class TestXbrlExport(AccountTestInvoicingCommon, HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        today = datetime.date.today()
        cls.year_start = today.replace(month=1, day=1)
        for amount, move_type in ((1000.0, 'out_invoice'), (250.0, 'in_invoice'), (100.0, 'out_refund')):
            move = cls.env['account.move'].create({
                'move_type': move_type, 'partner_id': cls.partner_a.id, 'invoice_date': today,
                'invoice_line_ids': [Command.create({'name': 'x', 'quantity': 1, 'price_unit': amount,
                                                     'tax_ids': []})]})
            move.action_post()
        group = cls.env.ref('account.group_account_readonly')
        cls.reader = cls.env['res.users'].create({
            'name': 'XBRL Reader', 'login': 'nc_xbrl_reader', 'password': 'nc_xbrl_reader_pw',
            'group_ids': [(6, 0, [group.id, cls.env.ref('base.group_user').id])]})
        cls.other = cls.env['res.users'].create({
            'name': 'XBRL Other', 'login': 'nc_xbrl_other', 'password': 'nc_xbrl_other_pw',
            'group_ids': [(6, 0, [group.id, cls.env.ref('base.group_user').id])]})

    def _facts(self, wizard):
        xml = self.env['ncollection.account.report.xbrl']._nc_build(wizard)
        root = etree.fromstring(xml)
        out = {}
        for el in root:
            if el.tag.startswith('{%s}' % NS['ifrs-full']):
                out.setdefault(el.tag.split('}')[1], []).append((el.get('contextRef'), float(el.text)))
        return root, out

    def test_balance_sheet_facts_are_the_report_totals(self):
        wizard = self.env['ncollection.account.report.balance.sheet'].create({'date_to': datetime.date.today()})
        root, facts = self._facts(wizard)
        assets, liabilities_equity = wizard._nc_totals()
        self.assertAlmostEqual(facts['Assets'][0][1], assets)
        self.assertAlmostEqual(facts['EquityAndLiabilities'][0][1], liabilities_equity)
        self.assertAlmostEqual(facts['Assets'][0][1], facts['EquityAndLiabilities'][0][1], msg="the books balance")
        self.assertAlmostEqual(facts['Assets'][0][1], facts['CurrentAssets'][0][1] + facts['NoncurrentAssets'][0][1])
        schema = root.find('{%s}schemaRef' % NS['link'])
        self.assertEqual(schema.get('{%s}href' % NS['xlink']), TAXONOMY_ENTRY_POINT)
        measure = root.find('.//{%s}measure' % NS['xbrli']).text
        self.assertEqual(measure, 'iso4217:' + self.env.company.currency_id.name)
        self.assertIsNotNone(root.find('.//{%s}instant' % NS['xbrli']))

    def test_profit_and_loss_facts_signs_and_unmapped_lines(self):
        wizard = self.env['ncollection.account.report.profit.loss'].create(
            {'date_from': self.year_start, 'date_to': datetime.date.today()})
        _root, facts = self._facts(wizard)
        figures = wizard._nc_totals()
        self.assertAlmostEqual(facts['Revenue'][0][1], figures['revenue'])
        lines = self.env['account.move.line'].search([
            ('account_id.account_type', '=', 'income'), ('parent_state', '=', 'posted'),
            ('company_id', '=', self.env.company.id),
            ('date', '>=', self.year_start), ('date', '<=', datetime.date.today())])
        self.assertAlmostEqual(facts['Revenue'][0][1], -sum(lines.mapped('balance')),
                               msg="independently: the posted income ledger for the period")
        self.assertGreater(facts['Revenue'][0][1], 0)
        self.assertAlmostEqual(facts['ProfitLoss'][0][1], figures['net_profit'])
        self.assertNotIn('GrossProfit', facts, "the report's gross profit includes other income: not IFRS GrossProfit")
        self.assertNotIn('OperatingExpense', facts)

    def test_cash_flow_facts_are_the_report_figures(self):
        wizard = self.env['ncollection.account.report.cash.flow'].create(
            {'date_from': self.year_start, 'date_to': datetime.date.today(), 'comparison_type': 'none'})
        root, facts = self._facts(wizard)
        figures = wizard._nc_totals()
        for concept, key in (('CashFlowsFromUsedInOperatingActivities', 'operating'),
                             ('CashFlowsFromUsedInInvestingActivities', 'investing'),
                             ('CashFlowsFromUsedInFinancingActivities', 'financing'),
                             ('IncreaseDecreaseInCashAndCashEquivalentsBeforeEffectOfExchangeRateChanges',
                              'net_change')):
            self.assertAlmostEqual(facts[concept][0][1], figures[key], msg=concept)
        cash = {ctx: value for ctx, value in facts['CashAndCashEquivalents']}
        self.assertEqual(len(cash), 2, "opening and closing instants")
        self.assertAlmostEqual(max(cash.items())[1] - min(cash.items())[1],
                               figures['cash_close'] - figures['cash_open'])
        self.assertNotIn('WorkingCapital', ''.join(facts))

    def test_changes_in_equity_total_column(self):
        today = datetime.date.today()
        wizard = self.env['ncollection.account.report.equity.changes'].create(
            {'date_from': self.year_start, 'date_to': today})
        root, facts = self._facts(wizard)
        opening, movement, closing = wizard._nc_totals()
        equity = sorted(facts['Equity'])                 # c_I<opening> sorts before c_I<closing>
        self.assertEqual(len(equity), 2)
        self.assertAlmostEqual(equity[0][1], opening)
        self.assertAlmostEqual(equity[1][1], closing)
        self.assertAlmostEqual(facts['ChangesInEquity'][0][1], movement)
        self.assertAlmostEqual(equity[0][1] + facts['ChangesInEquity'][0][1], equity[1][1])
        pl = self.env['ncollection.account.report.profit.loss'].create(
            {'date_from': self.year_start, 'date_to': today})
        self.assertAlmostEqual(facts['ProfitLoss'][0][1], pl._nc_totals()['net_profit'],
                               msg="independently: the Profit and Loss of the same period")
        bs = self.env['ncollection.account.report.balance.sheet'].create({'date_to': today})
        bs_equity = sum(bs._nc_bucket_totals(bs)[0][k] for k in ('equity', 'accumulated_earnings'))
        self.assertAlmostEqual(equity[1][1], bs_equity, msg="closing equity ties to the Balance Sheet")
        self.assertIsNone(root.find('.//{%s}segment' % NS['xbrli']), "no component member is claimed")

    def test_comparison_period_is_a_second_context(self):
        wizard = self.env['ncollection.account.report.profit.loss'].create(
            {'date_from': self.year_start, 'date_to': datetime.date.today(), 'comparison_type': 'previous_year'})
        _root, facts = self._facts(wizard)
        self.assertEqual(len({ctx for ctx, _v in facts['Revenue']}), 2)
        self.assertAlmostEqual(facts['Revenue'][1][1], 0.0, msg="nothing was invoiced last year: a zero, not a gap")
        wizard.comparison_type = 'none'
        _root, facts = self._facts(wizard)
        self.assertEqual(len(facts['Revenue']), 1)

    def test_route_downloads_and_refuses_what_it_must(self):
        wizard = self.env['ncollection.account.report.balance.sheet'].with_user(self.reader).create(
            {'date_to': datetime.date.today()})
        self.authenticate('nc_xbrl_reader', 'nc_xbrl_reader_pw')
        res = self.url_open(_ROUTE % (wizard._name, wizard.id))
        self.assertEqual(res.status_code, 200)
        self.assertIn(b'ifrs-full', res.content)
        self.assertIn('no-store', res.headers.get('Cache-Control', ''))
        trial = self.env['ncollection.account.report.trial.balance'].with_user(self.reader).create({}) \
            if 'ncollection.account.report.trial.balance' in self.env else None
        if trial:
            self.assertEqual(self.url_open(_ROUTE % (trial._name, trial.id)).status_code, 404, "no IFRS mapping")
        self.assertEqual(self.url_open(_ROUTE % ('res.users', 1)).status_code, 404, "forged model")
        self.authenticate('nc_xbrl_other', 'nc_xbrl_other_pw')
        self.assertIn(self.url_open(_ROUTE % (wizard._name, wizard.id)).status_code, (403, 404),
                      "another user's report run")
