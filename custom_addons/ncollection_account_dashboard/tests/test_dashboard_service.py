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
import inspect
from unittest.mock import patch

from dateutil.relativedelta import relativedelta

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
        # net_margin comes from the PROFITABILITY service, not the summary, so
        # checking only the summary would leave that one KPI unguarded against
        # a local recomputation.
        self._provenance(payload, 'ncollection.account.report.profitability')
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
        # Rows must mirror the ENGINE's real contract: a list of TUPLES ordered
        # groupby-then-aggregates (engine.py builds them with
        # `tuple(self._flatten_cell(cell) for cell in row)`), and a grouped
        # many2one always flattens to (id, label) — the null group being
        # (False, '').
        #
        # The first version of this fixture used dicts keyed by field name,
        # which matched the implementation's WRONG assumption rather than the
        # engine. It passed, and hid an AttributeError that would have fired on
        # the first tenant with real pipeline data. A mock is only worth as much
        # as its fidelity to the contract it stands in for.
        rows = {
            'pipeline': [
                ((7, 'Qualified'), 5000.0, 3),
                ((False, ''), 250.0, 1),          # the null group
            ],
            'top_customers': [
                ((11, 'Al Barari Trading'), 9000.0),
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
        # The null group arrives as (False, '') — a NON-EMPTY tuple, so a
        # truthiness test on it would never fall through. The label must be
        # substituted from the empty string, not left blank.
        self.assertEqual(funnel['rows'][1]['stage_id'], False)
        self.assertTrue(funnel['rows'][1]['label'],
                        "null group must get a human label, not ''")
        self.assertNotEqual(funnel['rows'][1]['label'], '')

        ranking = panels['top_customers']
        self.assertEqual(ranking['type'], 'ranking')
        self.assertEqual(ranking['rows'][0]['label'], 'Al Barari Trading')
        self.assertAlmostEqual(ranking['rows'][0]['value'], 9000.0)

    def test_licensed_but_empty_is_not_the_same_as_unlicensed(self):
        """Empty rows must render an empty PANEL; only None omits the panel.

        The engine answers None for "absent or unlicensed" and {'rows': []} for
        "licensed, nothing matched". Collapsing the two would make a licensed
        CEO with a quiet month indistinguishable from a Basic-plan tenant with
        no CRM at all — the exact distinction get_ceo_dashboard's docstring
        promises to preserve.
        """
        engine = self.env['ncollection.aggregation.engine']

        def empty_aggregate(spec):
            return {'key': spec['key'], 'rows': [], 'cached': False}

        with patch.object(type(engine), 'aggregate', side_effect=empty_aggregate):
            payload = self.service.get_ceo_dashboard()

        panels = {p['key']: p for p in payload['panels']}
        self.assertEqual(set(panels), {'pipeline', 'top_customers'},
                         "licensed-but-empty must still emit both panels")
        self.assertEqual(panels['pipeline']['rows'], [])
        self.assertEqual(panels['top_customers']['rows'], [])

        # ...and the contrast: None omits them entirely.
        with patch.object(type(engine), 'aggregate', side_effect=lambda spec: None):
            omitted = self.service.get_ceo_dashboard()
        self.assertEqual(omitted['panels'], [])

    def test_top_customers_is_bounded_to_the_reporting_period(self):
        """The leaderboard must carry the same date window as meta.period.

        An unbounded all-time ranking displayed next to a period-scoped KPI row
        is a figure the reader will mis-attribute to that period. Asserts on the
        SPEC the service hands the engine, which is the only place the bound
        exists.
        """
        engine = self.env['ncollection.aggregation.engine']
        seen = {}

        def capture(spec):
            seen[spec['key']] = spec
            return None

        with patch.object(type(engine), 'aggregate', side_effect=capture):
            payload = self.service.get_ceo_dashboard()

        # A domain carries TWO terms on date_order, so it must not be flattened
        # into a dict keyed by field — the second would silently overwrite the
        # first and the test would assert against only one bound.
        bounds = {t[1]: t[2] for t in seen['top_customers']['domain']
                  if t[0] == 'date_order'}
        self.assertEqual(set(bounds), {'>=', '<'},
                         "leaderboard needs BOTH a lower and an upper date bound")
        period = payload['meta']['period']
        self.assertEqual(bounds['>='], period['from'])
        # Upper bound is HALF-OPEN: date_order is a timestamp, so `<= date_to`
        # would coerce to midnight and silently drop the final day's orders.
        self.assertEqual(bounds['<'], period['to'] + relativedelta(days=1))

        # The funnel, by contrast, is current-state and must NOT be bounded.
        # Naming two specific fields would let a future `write_date` bound slip
        # through while the test still claimed the funnel was unbounded, so this
        # rejects ANY date term rather than an enumerated pair.
        dated = [t[0] for t in seen['pipeline']['domain'] if 'date' in t[0]]
        self.assertFalse(
            dated,
            "the funnel is a current-state view by design; a date bound would "
            "hide live deals opened before the window. Found: %s" % dated)

    def test_top_customers_cannot_derive_its_own_period(self):
        """The window must be PASSED IN, never re-derived inside the helper.

        Asserting the two dates merely have equal VALUES would still pass if the
        helper called context_today() itself, because within one request both
        derivations agree. This pins the structure instead: the helper has no
        default window, so it cannot silently drift from the meta.period shown
        next to it — the caller resolves the period once and threads it.

        Checks the SIGNATURE rather than catching a TypeError from calling it
        with no arguments. That weaker version passed even after defaults were
        added, because `None + relativedelta(days=1)` raises TypeError too — it
        proved only that *something* blew up, not that the window is required.
        """
        params = inspect.signature(type(self.service)._top_customers).parameters
        for name in ('date_from', 'date_to'):
            self.assertIs(
                params[name].default, inspect.Parameter.empty,
                "%s must stay REQUIRED; a default lets the helper re-derive a "
                "window that can differ from the one in meta.period" % name)
