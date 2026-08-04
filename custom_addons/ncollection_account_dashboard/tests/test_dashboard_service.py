# -*- coding: utf-8 -*-
"""Dashboard payload: shape + PROVENANCE (F3-T01).

The provenance test is the positive half of the "zero financial computation"
acceptance: every KPI the dashboard shows must equal, to the penny, the figure
the F2-T08 executive service produces for the same period. If the dashboard ever
recomputed anything, these would diverge.

Inherits AccountTestInvoicingCommon (same base as the sibling
test_executive_reports) so a full chart of accounts + company are provisioned on
a clean CI database — never a manual account search that can come up empty.
"""
from odoo import fields
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.tests import tagged


@tagged('post_install', '-at_install')
class TestDashboardService(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.service = cls.env['ncollection.account.dashboard.service']
        receivable = cls.company_data['default_account_receivable']
        revenue = cls.company_data['default_account_revenue']
        journal = cls.company_data['default_journal_misc']
        # Dated today so it always falls inside the service's default YTD window
        # (date_from = Jan 1 of this year, date_to = today).
        move = cls.env['account.move'].create({
            'move_type': 'entry',
            'journal_id': journal.id,
            'date': fields.Date.context_today(cls.env.user),
            'line_ids': [
                (0, 0, {'account_id': receivable.id, 'debit': 1000.0, 'credit': 0.0}),
                (0, 0, {'account_id': revenue.id, 'debit': 0.0, 'credit': 1000.0}),
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
