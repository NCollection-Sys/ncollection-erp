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
from unittest.mock import patch

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

    # ---- #56 CEO dashboard ------------------------------------------------

    def test_ceo_dashboard(self):
        payload = self.service.get_ceo_dashboard()
        self._assert_shape(payload)
        self._provenance(payload, 'ncollection.account.report.summary')
        # `panels` is the additive key #56 introduces; the contract stays
        # backward compatible because the other three never populate it.
        self.assertIn('panels', payload)
        self.assertIsInstance(payload['panels'], list)

    def test_ceo_dashboard_degrades_without_crm_or_sales(self):
        """The whole reason no sale/crm manifest dependency was added.

        This test database installs only the dashboard's real dependencies, so
        crm/sale are absent — exactly a Basic-plan tenant's situation. The
        financial half must still render and the cross-domain panels must be
        OMITTED rather than present-and-zero: a funnel showing 0 reads as "no
        pipeline", which is a business claim we have no basis to make.
        """
        for model in ('crm.lead', 'sale.order'):
            if model in self.env:
                self.skipTest('%s is installed here; this asserts the absent case' % model)
        payload = self.service.get_ceo_dashboard()
        self.assertEqual(payload['panels'], [], "panels must be omitted, not zeroed")
        # ...and the financial half is untouched.
        self.assertTrue(payload['kpis'])
        self.assertTrue(payload['charts'])
        self.assertTrue(any(k['value'] is not None for k in payload['kpis']),
                        "financial KPIs must still resolve without CRM/Sales")

    def test_ceo_panels_render_when_the_engine_returns_rows(self):
        """The transform from engine rows to panel rows, without needing crm
        or sale installed. Patches the ENGINE (P4-T01), not our own method, so
        the code under test is the real _pipeline_funnel/_top_customers."""
        engine = self.env['ncollection.aggregation.engine']
        rows = {
            'pipeline': [
                {'stage_id': (7, 'Qualified'), 'expected_revenue:sum': 5000.0, '__count': 3},
                {'stage_id': False, 'expected_revenue:sum': 250.0, '__count': 1},
            ],
            'top_customers': [
                {'partner_id': (11, 'Al Barari Trading'), 'amount_total:sum': 9000.0},
            ],
        }

        def fake_aggregate(spec):
            key = spec['key']
            return {'key': key, 'rows': rows[key], 'cached': False} if key in rows else None

        with patch.object(type(engine), 'aggregate', side_effect=fake_aggregate):
            payload = self.service.get_ceo_dashboard()

        panels = {p['key']: p for p in payload['panels']}
        self.assertEqual(set(panels), {'pipeline', 'top_customers'})

        funnel = panels['pipeline']
        self.assertEqual(funnel['type'], 'funnel')
        self.assertEqual(funnel['drilldown'], {'model': 'crm.lead', 'field': 'stage_id'})
        self.assertEqual(funnel['rows'][0]['label'], 'Qualified')
        self.assertAlmostEqual(funnel['rows'][0]['value'], 5000.0)
        self.assertEqual(funnel['rows'][0]['count'], 3)
        # The null group must not crash the unpack — the engine's _flatten_cell
        # returns an (id, label) pair for it too, but False arrives here when a
        # spec yields no group at all.
        self.assertEqual(funnel['rows'][1]['stage_id'], False)
        self.assertTrue(funnel['rows'][1]['label'])

        ranking = panels['top_customers']
        self.assertEqual(ranking['type'], 'ranking')
        self.assertEqual(ranking['rows'][0]['label'], 'Al Barari Trading')
        self.assertAlmostEqual(ranking['rows'][0]['value'], 9000.0)
