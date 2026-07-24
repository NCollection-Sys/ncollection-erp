# -*- coding: utf-8 -*-
"""Workspace config sync (P2-T03) — CI-safe unit tests.

Cover the pure logic without a real cross-DB round-trip: the desired-config
projection, the lifecycle/plan triggers, the enqueue gating, the json2/bearer
client's request shape + failure handling, and the reconcile selection. The
real platform->tenant json2 round-trip is proven by
scripts/provisioning/verify_config_sync.sh (evidence in the PR).
"""
from unittest.mock import MagicMock, patch

from odoo.tests import TransactionCase, tagged

SYNC = 'odoo.addons.ncollection_saas.models.config_sync'


@tagged('post_install', '-at_install')
class TestConfigSync(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['ir.config_parameter'].sudo().set_param(
            'ncollection_saas.provisioning_quota_per_hour', '0')
        cls.plan = cls.env['ncollection.subscription.plan'].create({
            'name': 'Growth', 'code': 'GROWTH',
            'allowed_module_names': 'crm,sale', 'max_users': 10})

    def _tenant(self, **kw):
        vals = {'company_name': 'Acme', 'plan_id': self.plan.id, 'status': 'trial',
                'database_status': 'ready', 'database_name': 'acme'}
        vals.update(kw)
        return self.env['ncollection.tenant'].create(vals)

    # ---- desired-config projection ---------------------------------------

    def test_desired_vals_project_tenant_status(self):
        tenant = self._tenant(status='suspended')
        vals = tenant._config_sync_vals()
        self.assertEqual(vals, {
            'allowed_module_names': 'crm,sale', 'plan_code': 'GROWTH',
            'subscription_status': 'suspended', 'max_users': 10,
        })

    # ---- enqueue gating --------------------------------------------------

    def test_enqueue_only_ready_tenants(self):
        ready = self._tenant(database_status='ready', database_name='r')
        notready = self._tenant(database_status='not_provisioned', database_name=False)
        before = self.env['queue.job'].search_count([])
        (ready + notready)._config_sync_enqueue()
        self.assertEqual(self.env['queue.job'].search_count([]), before + 1,
                         "only the ready tenant is enqueued")

    # ---- lifecycle + plan triggers --------------------------------------

    def test_tenant_status_action_enqueues(self):
        tenant = self._tenant(status='active', database_status='ready', database_name='a')
        before = self.env['queue.job'].search_count([])
        tenant.action_suspend()
        self.assertEqual(self.env['queue.job'].search_count([]), before + 1,
                         "suspending a tenant enqueues a config sync")

    def test_repeated_triggers_dedupe_per_tenant(self):
        # identity_key collapses rapid repeat triggers into ONE pending sync
        # (no redundant pushes for the same tenant).
        tenant = self._tenant(status='active', database_status='ready', database_name='d')
        before = self.env['queue.job'].search_count([])
        tenant._config_sync_enqueue()
        tenant._config_sync_enqueue()
        self.assertEqual(self.env['queue.job'].search_count([]), before + 1,
                         "duplicate pending syncs for one tenant are de-duplicated")

    def test_plan_edit_fans_out_to_tenants(self):
        self._tenant(database_status='ready', database_name='t1')
        self._tenant(database_status='ready', database_name='t2')
        before = self.env['queue.job'].search_count([])
        self.plan.write({'allowed_module_names': 'crm,sale,account'})
        self.assertEqual(self.env['queue.job'].search_count([]), before + 2,
                         "a plan edit fans out to both ready tenants")

    def test_subscription_plan_change_updates_tenant_and_enqueues(self):
        tenant = self._tenant(database_status='ready', database_name='s')
        sub = self.env['ncollection.subscription'].create({
            'name': 'SUB', 'tenant_id': tenant.id, 'plan_id': self.plan.id, 'status': 'draft'})
        tenant.subscription_id = sub.id
        plan2 = self.env['ncollection.subscription.plan'].create({
            'name': 'Ent', 'code': 'ENT', 'allowed_module_names': 'crm,sale,account,stock',
            'max_users': 50})
        before = self.env['queue.job'].search_count([])
        sub.write({'plan_id': plan2.id})
        self.assertEqual(tenant.plan_id, plan2, "tenant plan follows its current subscription")
        self.assertEqual(self.env['queue.job'].search_count([]), before + 1)

    # ---- json2/bearer client --------------------------------------------

    def test_push_builds_bearer_request(self):
        tenant = self._tenant(database_name='acme')
        vals = tenant._config_sync_vals()
        with patch.dict('os.environ', {'NC_CONFIG_SYNC_KEY': 'secret-key'}), \
                patch('%s.requests.post' % SYNC) as post:
            post.return_value = MagicMock(status_code=200, text='{}')
            ok = tenant._config_sync_push('acme', vals)
        self.assertTrue(ok)
        args, kwargs = post.call_args
        self.assertTrue(args[0].endswith('/json/2/ncollection.workspace.config/sync_from_platform'))
        self.assertEqual(kwargs['headers']['Authorization'], 'Bearer secret-key')
        self.assertEqual(kwargs['headers']['X-Odoo-Database'], 'acme')
        # The Host MUST carry the tenant subdomain: the receiver routes the DB by
        # Host under db_filter=^%d$, so `localhost` would select no DB -> 404 and
        # the sync would silently no-op (REGRESSIONS.md R-015). base_domain param
        # defaults to ncollectionerp.com (data/domain_data.xml).
        self.assertEqual(kwargs['headers']['Host'], 'acme.ncollectionerp.com')

    def test_push_without_key_is_soft_fail(self):
        tenant = self._tenant(database_name='acme')
        with patch.dict('os.environ', {}, clear=True), \
                patch('%s.requests.post' % SYNC) as post:
            ok = tenant._config_sync_push('acme', {})
        self.assertFalse(ok)
        post.assert_not_called()  # no key -> never even attempts the call

    def test_push_non_200_is_soft_fail(self):
        tenant = self._tenant(database_name='acme')
        with patch.dict('os.environ', {'NC_CONFIG_SYNC_KEY': 'k'}), \
                patch('%s.requests.post' % SYNC) as post:
            post.return_value = MagicMock(status_code=403, text='denied')
            self.assertFalse(tenant._config_sync_push('acme', {}))

    # ---- reconcile -------------------------------------------------------

    def test_reconcile_enqueues_ready_tenants(self):
        self._tenant(database_status='ready', database_name='r1')
        self._tenant(database_status='error', database_name='e1')  # not ready
        before = self.env['queue.job'].search_count([])
        self.env['ncollection.tenant']._cron_reconcile_config()
        self.assertGreaterEqual(self.env['queue.job'].search_count([]), before + 1)
