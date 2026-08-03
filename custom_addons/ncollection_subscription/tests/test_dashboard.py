# -*- coding: utf-8 -*-
"""SaaS admin dashboard (P2-T15).

Covers the delta added on top of the P1-T07 dashboard: MRR / churn / conversion,
the failed-provisioning + at-risk monitors, the pg_database_size storage read, the
stored subscription.mrr measure, and the Platform Admin group gating (UI restriction
mirrored at the ORM, Rule 7).
"""
from datetime import timedelta
from unittest.mock import patch

import itertools

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestSaasDashboard(TransactionCase):

    # Monotonic across the whole class: unique by construction, no hashing,
    # no modulo, and stable from run to run.
    _db_seq = itertools.count(1)

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Dashboard = cls.env['ncollection.subscription.dashboard']
        cls.plan = cls.env['ncollection.subscription.plan'].create({
            'name': 'Dash Growth', 'code': 'DASHGROWTH',
            'monthly_price': 100.0, 'yearly_price': 1200.0, 'max_users': 10})

    def _tenant(self, name, status='active', db=None):
        # database_status='ready' keeps a lifecycle change from triggering the
        # provisioning engine (subprocess) during the test.
        #
        # The name used to be `t%d % (abs(hash(name)) % 10000)`. Two faults in
        # one expression (#276):
        #   * str hashing is RANDOMISED per process (PYTHONHASHSEED), so the
        #     same test produced a different database_name on every run --
        #     the definition of an irreproducible fixture;
        #   * folding it into 10000 buckets meant two distinct names could
        #     land on the same one, and `unique(database_name)` (tenant.py:87)
        #     then failed the create. Rare, order-independent, and impossible
        #     to reproduce on demand -- which is exactly how it was reported.
        # A counter is deterministic and collision-free by construction, so
        # there is no probability left to argue about.
        return self.env['ncollection.tenant'].create({
            'company_name': name, 'plan_id': self.plan.id, 'status': status,
            'database_status': 'ready',
            'database_name': db or ('dash%d' % next(self._db_seq))})

    def _sub(self, tenant, status='active', cycle='monthly', end=None):
        return self.env['ncollection.subscription'].create({
            'name': 'S-%d' % tenant.id, 'tenant_id': tenant.id, 'plan_id': self.plan.id,
            'status': status, 'billing_cycle': cycle,
            'start_date': fields.Date.context_today(self.env.user), 'end_date': end})

    def test_fixture_db_names_are_unique_and_stable(self):
        """The guard for #276: fixture names must not be probabilistic.

        The old helper derived database_name from `abs(hash(name)) % 10000`.
        Python randomises str hashing per PROCESS, so the value moved every
        run, and 10000 buckets meant two distinct names could collide and trip
        `unique(database_name)`. It failed roughly one CI run in a few hundred
        and never reproduced on demand.

        Asserting "the last run happened to pass" would prove nothing. What is
        assertable is the PROPERTY that replaced the gamble: consecutive
        fixtures get distinct names, and none of them is hash-derived.
        """
        names = [self._tenant('Dup %d' % i).database_name for i in range(25)]
        self.assertEqual(len(set(names)), len(names),
                         "fixture database names collided: %s" % names)
        for n in names:
            self.assertRegex(
                n, r'^[a-z][a-z0-9]*$',
                "db names must stay alphanumeric — db_filter routes a "
                "subdomain to the same-named database")

    def test_mrr_field_normalization(self):
        """subscription.mrr normalizes plan price to a monthly amount (the graph measure)."""
        self.assertEqual(self._sub(self._tenant('M1'), cycle='monthly').mrr, 100.0)
        self.assertEqual(self._sub(self._tenant('M2'), cycle='yearly').mrr, 100.0)  # 1200/12

    def test_mrr_recomputes_on_plan_price_change(self):
        """The stored mrr tracks plan price + billing cycle changes (@api.depends)."""
        sub = self._sub(self._tenant('Recompute Co'), cycle='monthly')
        self.assertEqual(sub.mrr, 100.0)
        self.plan.monthly_price = 250.0
        self.assertEqual(sub.mrr, 250.0)
        sub.billing_cycle = 'yearly'
        self.assertEqual(sub.mrr, 100.0)  # 1200/12

    def test_dashboard_kpis(self):
        self._sub(self._tenant('A1'), status='active', cycle='monthly')   # +100 MRR
        self._sub(self._tenant('A2'), status='active', cycle='yearly')    # +100 MRR
        d = self.Dashboard.create({})
        self.assertGreaterEqual(d.total_tenants, 2)
        self.assertGreaterEqual(d.active_subscriptions, 2)
        self.assertGreaterEqual(d.monthly_revenue, 200.0)   # MRR sums active subs
        self.assertTrue(0.0 <= d.churn_rate <= 100.0)
        self.assertTrue(0.0 <= d.trial_conversion_rate <= 100.0)

    def test_growth_counts_cancelled_as_churn(self):
        base = self.Dashboard.create({}).churned_subscriptions
        self._sub(self._tenant('C1', status='suspended'), status='cancelled')
        self.assertEqual(self.Dashboard.create({}).churned_subscriptions, base + 1)

    def test_monitors_surface_failed_and_at_risk(self):
        t = self._tenant('Fail Co')
        job = self.env['ncollection.provisioning.job'].create({
            'tenant_id': t.id, 'database_name': 'failco', 'status': 'failed'})
        soon = fields.Date.context_today(self.env.user) + timedelta(days=10)
        expiring = self._sub(self._tenant('Expiring Co'), status='active', end=soon)
        d = self.Dashboard.create({})
        self.assertIn(job, d.failed_job_ids)
        self.assertIn(expiring, d.at_risk_subscription_ids)

    def test_storage_reader_reads_the_cluster(self):
        """pg_database_size infra read (Rule 3-safe) returns cluster stats."""
        d = self.Dashboard.create({})
        self.assertGreaterEqual(d.db_count, 1)
        self.assertGreater(d.db_storage_total_mb, 0.0)
        self.assertIn('<table', d.db_storage_html or '')

    def test_storage_degrades_gracefully_on_connection_failure(self):
        """A maintenance-connection failure must never break the dashboard."""
        from ..models import dashboard as dash_mod
        dash_mod._db_size_cache.update(ts=0.0, value=([], 0, None))  # bypass any cached success
        with patch('odoo.addons.ncollection_subscription.models.dashboard.psycopg2.connect',
                   side_effect=Exception('boom')):
            d = self.Dashboard.create({})
            self.assertEqual(d.db_count, 0)
            self.assertEqual(d.db_storage_total_mb, 0.0)
            self.assertIn('unavailable', d.db_storage_html or '')

    def test_platform_admin_group_mirrored_and_gated(self):
        group = self.env.ref('ncollection_subscription.group_platform_admin')
        self.assertIn(group, self.env.ref('base.group_system').implied_ids)
        # a plain internal user (no platform-admin) is denied the dashboard at the ORM
        user = self.env['res.users'].create({
            'name': 'Plain', 'login': 'plain_dash_user',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        with self.assertRaises(AccessError):
            self.Dashboard.with_user(user).create({}).total_tenants
        # the operational models behind the dashboard are gated too (not just the
        # dashboard transient) — the ORM mirror covers tenant/subscription reads.
        with self.assertRaises(AccessError):
            self.env['ncollection.tenant'].with_user(user).search([], limit=1).mapped('company_name')
        with self.assertRaises(AccessError):
            self.env['ncollection.subscription'].with_user(user).search([], limit=1).mapped('name')
