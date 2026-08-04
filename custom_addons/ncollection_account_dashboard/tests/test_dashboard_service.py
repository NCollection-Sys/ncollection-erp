# -*- coding: utf-8 -*-
"""Dashboard payload: shape + PROVENANCE (F3-T01).

The provenance test is the positive half of the "zero financial computation"
acceptance: every KPI the dashboard shows must equal, to the penny, the figure
the F2-T08 executive service produces for the same period. If the dashboard ever
recomputed anything, these would diverge.
"""
from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestDashboardService(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.service = cls.env['ncollection.account.dashboard.service']
        Account = cls.env['account.account']

        def acc(*types):
            # Odoo 19: account.account is multi-company; type is enough here.
            return Account.search([('account_type', 'in', types)], limit=1)

        cash = acc('asset_cash', 'asset_current')
        income = acc('income')
        journal = cls.env['account.journal'].search(
            [('type', '=', 'general'), ('company_id', '=', cls.env.company.id)], limit=1)
        assert cash and income and journal, \
            "test needs a chart of accounts (asset/income) + a general journal"
        move = cls.env['account.move'].create({
            'move_type': 'entry',
            'journal_id': journal.id,
            'date': fields.Date.context_today(cls.env.user),
            'line_ids': [
                (0, 0, {'account_id': cash.id, 'debit': 1000.0, 'credit': 0.0}),
                (0, 0, {'account_id': income.id, 'debit': 0.0, 'credit': 1000.0}),
            ],
        })
        move.action_post()

    def _assert_shape(self, payload):
        self.assertGreaterEqual(set(payload), {'kpis', 'charts', 'meta'})
        for kpi in payload['kpis']:
            self.assertGreaterEqual(set(kpi), {'key', 'label', 'value', 'previous', 'unit'})
        for chart in payload['charts']:
            self.assertGreaterEqual(set(chart), {'key', 'label', 'type', 'labels', 'series'})
        self.assertIn('currency', payload['meta'])
        self.assertIn('period', payload['meta'])

    def _provenance(self, payload, service_model):
        """Every KPI value equals the executive service's figure for that key."""
        expected = self.env[service_model].new({})._nc_service_figures()
        got = {k['key']: k['value'] for k in payload['kpis']}
        for key, value in expected.items():
            if key in got:
                self.assertAlmostEqual(
                    got[key], value, places=2,
                    msg="KPI %s must come from the service, not a recomputation" % key)

    def test_finance_dashboard(self):
        payload = self.service.get_finance_dashboard()
        self._assert_shape(payload)
        self._provenance(payload, 'ncollection.account.report.summary')

    def test_accountant_dashboard(self):
        payload = self.service.get_accountant_dashboard()
        self._assert_shape(payload)
        self._provenance(payload, 'ncollection.account.report.profitability')

    def test_cash_dashboard(self):
        payload = self.service.get_cash_dashboard()
        self._assert_shape(payload)
        self._provenance(payload, 'ncollection.account.report.summary')
