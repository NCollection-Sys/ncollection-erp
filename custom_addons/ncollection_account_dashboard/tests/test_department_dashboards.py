# -*- coding: utf-8 -*-
"""Department dashboards (#57 / P4-T04): shape, provenance, role visibility.

The department dashboards read every figure from the P4-T02 KPI service
(ncollection.kpi) and the P4-T01 aggregation engine — never a direct
sale/crm/hr/stock query (test_boundary enforces that statically). Here we prove:

* the payload keeps the {kpis, charts, panels, meta} contract;
* with the backing apps absent (they are not installed in this test DB), KPIs
  and panels degrade to empty — no crash, no misleading zero (plan-gating);
* panel rows and KPI cards are a faithful PASS-THROUGH of the service output
  (no recomputation);
* each dashboard menu is visible ONLY to its role group (the acceptance).
"""
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestDepartmentDashboards(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.service = cls.env['ncollection.account.dashboard.service']

    # ---- contract + graceful plan-gating ---------------------------------

    def _assert_shape(self, payload):
        self.assertGreaterEqual(set(payload), {'kpis', 'charts', 'panels', 'meta'})
        self.assertIsInstance(payload['kpis'], list)
        self.assertIsInstance(payload['panels'], list)
        self.assertIn('currency', payload['meta'])

    def test_all_three_shape_and_graceful_absent(self):
        # sale/crm/hr/stock are NOT installed in this test DB, so every KPI and
        # panel source returns None -> omitted. The dashboards must still return
        # a valid, non-crashing payload with empty kpis/panels.
        for method in ('get_sales_dashboard', 'get_hr_dashboard', 'get_warehouse_dashboard'):
            payload = getattr(self.service, method)()
            self._assert_shape(payload)
            self.assertEqual(payload['kpis'], [], "%s: KPI must be omitted when its app is absent" % method)
            self.assertEqual(payload['panels'], [], "%s: panels must be omitted when apps are absent" % method)

    # ---- provenance: rows/cards pass through the service verbatim ---------

    def test_panel_rows_are_engine_passthrough(self):
        Engine = type(self.env['ncollection.aggregation.engine'])
        # A ranking spec -> engine rows are (group_cell, value) tuples.
        self.patch(Engine, 'aggregate', lambda eng, spec: {
            'key': spec['key'], 'cached': False,
            'rows': [((7, 'Alice'), 5000.0), ((9, 'Bob'), 3000.0)],
        })
        rows = self.service._ranking_panel({
            'key': 'leaderboard', 'model': 'sale.order', 'groupby': ['user_id'],
            'aggregates': ['amount_total:sum'],
        })
        self.assertEqual(rows, [
            {'id': 7, 'label': 'Alice', 'value': 5000.0},
            {'id': 9, 'label': 'Bob', 'value': 3000.0},
        ])

    def test_panel_none_when_source_absent(self):
        Engine = type(self.env['ncollection.aggregation.engine'])
        self.patch(Engine, 'aggregate', lambda eng, spec: None)
        self.assertIsNone(self.service._ranking_panel({
            'key': 'x', 'model': 'sale.order', 'groupby': ['user_id'],
            'aggregates': ['amount_total:sum']}))

    def test_kpi_card_is_service_passthrough(self):
        Kpi = type(self.env['ncollection.kpi'])
        # ncollection.kpi.compute() owns value/previous/target; the dashboard
        # only relabels + maps the unit — it never derives a figure.
        self.patch(Kpi, 'compute', lambda kpi, reference=None: {
            'key': 'avg_deal_size', 'name': 'Average Deal Size', 'unit': 'currency',
            'value': 1234.0, 'previous': 1000.0, 'delta_pct': 23.4,
            'band': None, 'band_name': None, 'target': 2000.0,
            'period_start': None, 'period_end': None,
        })
        card = self.service._department_kpi('avg_deal_size', 'Average Deal Size')
        self.assertEqual(card['value'], 1234.0)
        self.assertEqual(card['previous'], 1000.0)
        self.assertEqual(card['unit'], 'currency')
        self.assertIn('2000', card['sub'])   # target surfaced from the service

    def test_kpi_omitted_when_value_none(self):
        Kpi = type(self.env['ncollection.kpi'])
        self.patch(Kpi, 'compute', lambda kpi, reference=None: {
            'value': None, 'previous': None, 'unit': 'ratio', 'target': 0.0})
        self.assertIsNone(
            self.service._department_kpi('inventory_turnover', 'Inventory Turnover'))

    # ---- acceptance: visible only to its role group ----------------------

    def test_menu_role_visibility(self):
        cases = [
            ('menu_sales_dashboard', 'group_role_sales'),
            ('menu_hr_dashboard', 'group_role_hr'),
            ('menu_warehouse_dashboard', 'group_role_warehouse'),
        ]
        for menu_xmlid, group_xmlid in cases:
            menu = self.env.ref('ncollection_account_dashboard.%s' % menu_xmlid)
            group = self.env.ref('ncollection_core.%s' % group_xmlid)
            self.assertEqual(
                menu.groups_id, group,
                "%s must be gated to exactly %s" % (menu_xmlid, group_xmlid))

    def test_financial_dashboards_untouched(self):
        # Guard against #57 regressing the existing dashboards' contract (#4):
        # they still return the same top-level keys and populate kpis.
        for method in ('get_finance_dashboard', 'get_accountant_dashboard',
                       'get_cash_dashboard'):
            payload = getattr(self.service, method)()
            self.assertGreaterEqual(set(payload), {'kpis', 'charts', 'meta'})
            self.assertTrue(payload['kpis'])
