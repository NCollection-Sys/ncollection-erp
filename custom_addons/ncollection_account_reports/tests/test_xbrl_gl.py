# -*- coding: utf-8 -*-
"""Trial Balance as XBRL Global Ledger (owner review 2026-11-18): the file carries EXACTLY the report's rows, under
the report's filters, in the shape of XBRL International's own trial-balance sample (beginning balance, period debit
and credit, ending balance per account, amounts never negative, the sign as D / C). Schema validity against the
official taxonomy is Arelle's job (docs/markdown/XBRL_EXPORT.md); these tests pin what the module decides."""
import datetime
from decimal import Decimal

from lxml import etree

from odoo import Command
from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.addons.ncollection_account_reports.models.xbrl_gl import GL_ENTRY_POINT, GL_NS

COR = GL_NS['gl-cor']


@tagged('post_install', '-at_install')
class TestTrialBalanceXbrlGl(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.today = datetime.date.today()
        cls.year_start = cls.today.replace(month=1, day=1)
        cls.moves = cls.env['account.move']
        for amount, move_type in ((1000.0, 'out_invoice'), (250.0, 'in_invoice'), (100.0, 'out_refund')):
            move = cls.env['account.move'].create({
                'move_type': move_type, 'partner_id': cls.partner_a.id, 'invoice_date': cls.today,
                'invoice_line_ids': [Command.create({'name': 'x', 'quantity': 1, 'price_unit': amount,
                                                     'tax_ids': []})]})
            move.action_post()
            cls.moves |= move

    def _parse(self, wizard):
        root = etree.fromstring(self.env['ncollection.account.report.xbrl']._nc_build(wizard))
        per = {}
        for detail in root.iter('{%s}entryDetail' % COR):
            code = detail.find('{%s}account/{%s}accountMainID' % (COR, COR)).text
            amount = Decimal(detail.find('{%s}amount' % COR).text)
            dc = detail.find('{%s}debitCreditCode' % COR).text
            include = detail.find('{%s}xbrlInfo/{%s}xbrlInclude' % (COR, COR)).text
            self.assertGreaterEqual(amount, 0, "the sign is the D / C code, never a negative amount")
            entry = per.setdefault(code, {})
            if include == 'period_change':
                entry['debit' if dc == 'D' else 'credit'] = amount
            else:
                entry[include] = amount if dc == 'D' else -amount
        return root, per

    def _check(self, wizard):
        root, per = self._parse(wizard)
        rows = [row for row in wizard._nc_compute_lines() if row.get('account_id')]
        self.assertEqual(len(per), len(rows), "every account of the report, none added, none omitted")
        for row in rows:
            code = self.env['account.account'].browse(row['account_id']).code
            entry = per[code]
            self.assertAlmostEqual(float(entry['beginning_balance']), row['opening_balance'], places=2)
            self.assertAlmostEqual(float(entry['debit']), row['debit'], places=2)
            self.assertAlmostEqual(float(entry['credit']), row['credit'], places=2)
            self.assertAlmostEqual(float(entry['ending_balance']), row['closing_balance'], places=2)
            self.assertEqual(entry['beginning_balance'] + entry['debit'] - entry['credit'], entry['ending_balance'])
        total = [row for row in wizard._nc_compute_lines() if not row.get('account_id')][0]
        self.assertAlmostEqual(float(sum(e['debit'] for e in per.values())), total['debit'], places=2)
        self.assertAlmostEqual(float(sum(e['credit'] for e in per.values())), total['credit'], places=2)
        return root, per

    def test_the_file_is_the_report(self):
        wizard = self.env['ncollection.account.report.trial.balance'].create(
            {'date_from': self.year_start, 'date_to': self.today})
        root, per = self._check(wizard)
        self.assertTrue(per)
        self.assertEqual(root.find('{%s}schemaRef' % GL_NS['link']).get('{%s}href' % GL_NS['xlink']), GL_ENTRY_POINT)
        self.assertEqual(root.find('.//{%s}entriesType' % COR).text, 'trialbalance')
        self.assertEqual(root.find('.//{%s}periodCoveredStart' % COR).text, self.year_start.isoformat())
        self.assertEqual(root.find('.//{%s}periodCoveredEnd' % COR).text, self.today.isoformat())
        self.assertEqual(root.find('.//{%s}measure' % GL_NS['xbrli']).text,
                         'iso4217:' + self.env.company.currency_id.name)
        self.assertTrue(any(e['ending_balance'] < 0 for e in per.values()), "a credit balance is written as C")

    def test_the_filters_of_the_report_are_the_filters_of_the_file(self):
        receivable = self.moves[0].line_ids.filtered(lambda l: l.account_id.account_type == 'asset_receivable').account_id
        wizard = self.env['ncollection.account.report.trial.balance'].create(
            {'date_from': self.year_start, 'date_to': self.today, 'account_ids': [(6, 0, receivable.ids)]})
        root, per = self._check(wizard)
        self.assertEqual(set(per), {receivable.code}, "only the filtered account")
        self.assertIn('accounts:', root.find('.//{%s}entriesComment' % COR).text)

    def _ledger_groups(self, wizard):
        root = etree.fromstring(self.env['ncollection.account.report.xbrl']._nc_build(wizard))
        self.assertEqual(root.find('.//{%s}entriesType' % COR).text, 'ledger')
        groups = []
        for header in root.iter('{%s}entryHeader' % COR):
            g = {'begin': None, 'end': None, 'debit': Decimal(0), 'credit': Decimal(0), 'items': 0}
            for detail in header.iter('{%s}entryDetail' % COR):
                amount = Decimal(detail.find('{%s}amount' % COR).text)
                dc = detail.find('{%s}debitCreditCode' % COR).text
                inc = detail.find('{%s}xbrlInfo/{%s}xbrlInclude' % (COR, COR))
                if inc is not None:
                    g['begin' if inc.text == 'beginning_balance' else 'end'] = amount if dc == 'D' else -amount
                else:
                    g['items'] += 1
                    self.assertIsNotNone(detail.find('{%s}documentNumber' % COR), "the entry number travels")
                    g['debit' if dc == 'D' else 'credit'] += amount
            self.assertEqual(g['begin'] + g['debit'] - g['credit'], g['end'], "each group reconciles")
            groups.append(g)
        return groups

    def test_general_and_partner_ledgers_are_the_reports(self):
        for model, key in (('ncollection.account.report.general.ledger', 'account_id'),
                           ('ncollection.account.report.partner.ledger', 'partner_id')):
            with self.subTest(report=model):
                wizard = self.env[model].create({'date_from': self.year_start, 'date_to': self.today})
                rows = wizard._nc_compute_lines()
                groups = self._ledger_groups(wizard)
                self.assertEqual(len(groups), len({r.get(key) or False for r in rows}), "one block per group")
                items = [r for r in rows if not r.get('is_initial')]
                self.assertEqual(sum(g['items'] for g in groups), len(items), "every journal item, none added")
                self.assertAlmostEqual(float(sum(g['debit'] for g in groups)),
                                       sum(r['debit'] for r in items), places=2)
                self.assertAlmostEqual(float(sum(g['credit'] for g in groups)),
                                       sum(r['credit'] for r in items), places=2)

    def test_the_viewer_exports_it_and_the_route_serves_it(self):
        Service = self.env['ncollection.report.service']
        run = Service.nc_report_run('ncollection.account.report.trial.balance',
                                    {'date_from': str(self.year_start), 'date_to': str(self.today)})
        self.assertFalse(run['xbrl_unavailable'])
        action = Service.nc_report_export(run['wizard']['model'], run['wizard']['id'], 'xbrl')
        self.assertEqual(action['type'], 'ir.actions.act_url')
        self.assertIn('/xbrl/ncollection.account.report.trial.balance/', action['url'])
