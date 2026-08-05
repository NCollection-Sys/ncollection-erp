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

    def test_all_three_shape(self):
        # Shape only — whether kpis/panels are populated depends on which apps
        # the CI DB installs (hr/crm/sale are present here), so we assert the
        # contract holds and nothing crashes, not that they are empty.
        for method in ('get_sales_dashboard', 'get_hr_dashboard', 'get_warehouse_dashboard'):
            self._assert_shape(getattr(self.service, method)())

    def test_graceful_absent(self):
        # Deterministic plan-gating: force BOTH service layers to report the
        # backing app absent/unlicensed (engine -> None, KPI value -> None) and
        # assert every KPI and panel is omitted — no crash, no misleading zero.
        # This does not depend on the CI DB's installed-app set.
        Engine = type(self.env['ncollection.aggregation.engine'])
        Kpi = type(self.env['ncollection.kpi'])
        self.patch(Engine, 'aggregate', lambda eng, spec: None)
        self.patch(Kpi, 'compute', lambda kpi, reference=None: {
            'value': None, 'previous': None, 'unit': 'ratio', 'target': 0.0})
        for method in ('get_sales_dashboard', 'get_hr_dashboard', 'get_warehouse_dashboard'):
            payload = getattr(self.service, method)()
            self._assert_shape(payload)
            self.assertEqual(payload['kpis'], [], "%s: KPI must be omitted when absent" % method)
            self.assertEqual(payload['panels'], [], "%s: panels must be omitted when absent" % method)

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

    def test_panel_units_are_currency_or_number(self):
        # HIGH fix: count/quantity panels must carry unit='number' so the view
        # never prefixes a headcount or a units-moved figure with a currency
        # symbol; monetary panels stay 'currency'. Patch the engine so the panel
        # specs return rows but the KPIs' own internal specs return None (omitted).
        Engine = type(self.env['ncollection.aggregation.engine'])
        panel_keys = {'headcount', 'leave', 'valuation', 'movement', 'leaderboard'}

        def fake(eng, spec):
            key = spec.get('key')
            if key in panel_keys:
                return {'key': key, 'cached': False, 'rows': [((1, 'X'), 3.0)]}
            if key == 'pipeline':
                return {'key': key, 'cached': False, 'rows': [((1, 'New'), 1000.0, 2)]}
            return None

        self.patch(Engine, 'aggregate', fake)
        expected = {
            'get_sales_dashboard': {'pipeline': 'currency', 'leaderboard': 'currency'},
            'get_hr_dashboard': {'headcount': 'number', 'leave': 'number'},
            'get_warehouse_dashboard': {'valuation': 'currency', 'movement': 'number'},
        }
        for method, units in expected.items():
            panels = {p['key']: p for p in getattr(self.service, method)()['panels']}
            for key, unit in units.items():
                self.assertIn(key, panels, "%s should render the %s panel" % (method, key))
                self.assertEqual(panels[key]['unit'], unit,
                                 "%s/%s must be %r" % (method, key, unit))

    def test_populated_panels_survive_absent_kpi(self):
        # HIGH fix: a dashboard with panel data but NO KPI (a common partial-
        # licensing case) must still expose its panels, not collapse to empty.
        Engine = type(self.env['ncollection.aggregation.engine'])
        self.patch(Engine, 'aggregate', lambda eng, spec: (
            {'key': spec.get('key'), 'cached': False, 'rows': [((1, 'X'), 3.0)]}
            if spec.get('key') in ('headcount', 'leave') else None))
        payload = self.service.get_hr_dashboard()
        self.assertEqual(payload['kpis'], [])           # KPI omitted...
        self.assertTrue(payload['panels'])              # ...but panels remain

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
        # Odoo 19: ir.ui.menu's groups relation is `group_ids` (renamed from
        # groups_id — see odoo19-gotchas). Assert each dashboard is visible to
        # its OWN role and to NO other department role, which proves "visible
        # only to its role group" robustly even if group_ids ever carried
        # implied groups.
        roles = {
            'menu_sales_dashboard': 'group_role_sales',
            'menu_hr_dashboard': 'group_role_hr',
            'menu_warehouse_dashboard': 'group_role_warehouse',
        }
        dept_groups = {
            xmlid: self.env.ref('ncollection_core.%s' % xmlid)
            for xmlid in roles.values()
        }
        for menu_xmlid, group_xmlid in roles.items():
            menu = self.env.ref('ncollection_account_dashboard.%s' % menu_xmlid)
            own = dept_groups[group_xmlid]
            self.assertIn(own, menu.group_ids,
                          "%s must be visible to %s" % (menu_xmlid, group_xmlid))
            for other_xmlid, other in dept_groups.items():
                if other_xmlid != group_xmlid:
                    self.assertNotIn(
                        other, menu.group_ids,
                        "%s must NOT be visible to %s" % (menu_xmlid, other_xmlid))

    def test_financial_dashboards_untouched(self):
        # Guard against #57 regressing the existing dashboards' contract (#4):
        # they still return the same top-level keys and populate kpis.
        for method in ('get_finance_dashboard', 'get_accountant_dashboard',
                       'get_cash_dashboard'):
            payload = getattr(self.service, method)()
            self.assertGreaterEqual(set(payload), {'kpis', 'charts', 'meta'})
            self.assertTrue(payload['kpis'])
