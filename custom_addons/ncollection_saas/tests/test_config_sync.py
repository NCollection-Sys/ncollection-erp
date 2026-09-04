# -*- coding: utf-8 -*-
"""Workspace config sync (P2-T03) — CI-safe unit tests.

Cover the pure logic without a real cross-DB round-trip: the desired-config
projection, the lifecycle/plan triggers, the enqueue gating, the json2/bearer
client's request shape + failure handling, and the reconcile selection. The
real platform->tenant json2 round-trip is proven by
scripts/provisioning/verify_config_sync.sh (evidence in the PR).
"""
import os

from unittest.mock import MagicMock, patch

from odoo.tests import TransactionCase, tagged

from ..models.config_sync import derive_tenant_key

SYNC = 'odoo.addons.ncollection_saas.models.config_sync'


@tagged('post_install', '-at_install')
class TestConfigSync(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['ir.config_parameter'].sudo().set_param(
            'ncollection_saas.provisioning_quota_per_hour', '0')
        cls.plan = cls.env['ncollection.subscription.plan'].create({
            'name': 'Growth', 'code': 'SYNCGROWTH',
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
            'allowed_module_names': 'crm,sale', 'plan_code': 'SYNCGROWTH',
            'subscription_status': 'suspended', 'max_users': 10,
        })

    # ---- enqueue gating --------------------------------------------------

    def test_enqueue_only_ready_tenants(self):
        ready = self._tenant(database_status='ready', database_name='syncrdy')
        notready = self._tenant(database_status='not_provisioned', database_name=False)
        before = self.env['queue.job'].search_count([])
        (ready + notready)._config_sync_enqueue()
        self.assertEqual(self.env['queue.job'].search_count([]), before + 1,
                         "only the ready tenant is enqueued")

    # ---- lifecycle + plan triggers --------------------------------------

    def test_tenant_status_action_enqueues(self):
        tenant = self._tenant(status='active', database_status='ready', database_name='syncact')
        before = self.env['queue.job'].search_count([])
        tenant.action_suspend()
        self.assertEqual(self.env['queue.job'].search_count([]), before + 1,
                         "suspending a tenant enqueues a config sync")

    def test_repeated_triggers_dedupe_per_tenant(self):
        # identity_key collapses rapid repeat triggers into ONE pending sync
        # (no redundant pushes for the same tenant).
        tenant = self._tenant(status='active', database_status='ready', database_name='syncdedup')
        before = self.env['queue.job'].search_count([])
        tenant._config_sync_enqueue()
        tenant._config_sync_enqueue()
        self.assertEqual(self.env['queue.job'].search_count([]), before + 1,
                         "duplicate pending syncs for one tenant are de-duplicated")

    def test_plan_edit_fans_out_to_tenants(self):
        """Scoped to CONFIG-SYNC jobs (#461). A plan edit that ADDS a module now
        also queues a module install per ready tenant, so a bare
        `search_count([])` counts both lifecycles and no longer says anything
        about the one this test is named for. The install half has its own
        coverage in test_module_install.py."""
        self._tenant(database_status='ready', database_name='syncfana')
        self._tenant(database_status='ready', database_name='syncfanb')
        sync_jobs = [('method_name', '=', 'sync_workspace_config')]
        before = self.env['queue.job'].search_count(sync_jobs)
        self.plan.write({'allowed_module_names': 'crm,sale,account'})
        self.assertEqual(self.env['queue.job'].search_count(sync_jobs), before + 2,
                         "a plan edit fans out to both ready tenants")

    def test_subscription_plan_change_updates_tenant_and_enqueues(self):
        tenant = self._tenant(database_status='ready', database_name='syncsub')
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
        # #212: the bearer is the PER-TENANT derived key (HMAC of master + db name),
        # never the raw master — so a leaked key is scoped to this one tenant.
        self.assertEqual(kwargs['headers']['Authorization'],
                         'Bearer %s' % derive_tenant_key('secret-key', 'acme'))
        self.assertEqual(kwargs['headers']['X-Odoo-Database'], 'acme')
        # The Host MUST carry the tenant subdomain: the receiver routes the DB by
        # Host under db_filter=^%d$, so `localhost` would select no DB -> 404 and
        # the sync would silently no-op (REGRESSIONS.md R-015). base_domain param
        # defaults to ncollectionerp.com (data/domain_data.xml).
        self.assertEqual(kwargs['headers']['Host'], 'acme.ncollectionerp.com')

    def test_derive_tenant_key_is_per_tenant(self):
        """#212: each tenant's bearer key is a unique, deterministic derivation of
        the platform master, so a leaked key is scoped to one tenant, not all."""
        master = 'master-secret'
        self.assertEqual(derive_tenant_key(master, 'clienta'),
                         derive_tenant_key(master, 'clienta'))       # deterministic
        self.assertNotEqual(derive_tenant_key(master, 'clienta'),
                            derive_tenant_key(master, 'clientb'))     # per-tenant
        self.assertNotEqual(derive_tenant_key(master, 'clienta'), master)  # not the raw master

    def test_derive_tenant_key_known_vector(self):
        """#212: pin the EXACT byte-level formula with a golden vector. The seed
        stores hash(derived) once at provisioning; the push re-derives on every
        sync. If a refactor silently changes the label/algorithm/encoding, both
        sides still agree with each other and every same-function test stays green
        — but every ALREADY-provisioned tenant's stored hash stops matching and
        config-sync 401s fleet-wide, visible only in prod logs. This frozen vector
        makes such a change fail CI instead."""
        self.assertEqual(derive_tenant_key('master-secret', 'clienta'),
                         'uyQCg5mY0QhZMWuDN5JK0mTwCu0C7pujjoRb0uV-4Ng=')

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
        self._tenant(database_status='ready', database_name='syncrecon')
        self._tenant(database_status='error', database_name='e1')  # not ready (exempt)
        before = self.env['queue.job'].search_count([])
        self.env['ncollection.tenant']._cron_reconcile_config()
        self.assertGreaterEqual(self.env['queue.job'].search_count([]), before + 1)


@tagged('post_install', '-at_install')
class TestInternalBaseUrlPrecedence(TransactionCase):
    """Where a config-sync push is addressed, per container (#465).

    The answer depends on WHICH PROCESS is pushing, not on admin preference:
    from the odoo container `localhost:8069` is the multi-tenant server, but
    from the provisioning-runner it is the runner's OWN server, scoped
    `-d <platform> --db-filter=^<platform>$`. A push carrying a tenant's Host
    header matched no database there and Odoo answered "No database is
    selected" -> HTTP 404, so EVERY config sync from the runner failed while
    module installs (subprocesses, not HTTP) succeeded.

    Invisible until #463 made the runner actually run, which is why the env
    override and these tests exist.
    """

    def setUp(self):
        super().setUp()
        self.Tenant = self.env['ncollection.tenant']
        self.env['ir.config_parameter'].sudo().set_param(
            'ncollection_saas.internal_base_url', 'http://param-host:8069')

    def _clear_env(self):
        os.environ.pop('NC_INTERNAL_BASE_URL', None)

    def test_the_env_var_wins_when_set(self):
        """The deployment fact beats the global parameter — this is what
        points the runner at the multi-tenant server."""
        os.environ['NC_INTERNAL_BASE_URL'] = 'http://odoo:8069'
        self.addCleanup(self._clear_env)
        self.assertEqual(self.Tenant._config_sync_base_url(), 'http://odoo:8069')

    def test_the_parameter_is_used_when_the_env_var_is_absent(self):
        """The admin knob still works — the override is additive, so an
        existing deployment that sets nothing is unaffected."""
        self._clear_env()
        self.assertEqual(self.Tenant._config_sync_base_url(),
                         'http://param-host:8069')

    def test_an_empty_or_whitespace_env_var_does_not_win(self):
        """An unset-but-present variable (a common compose accident) must not
        silently blank the endpoint — that would turn a misconfiguration into
        a request to nowhere."""
        for blank in ('', '   '):
            os.environ['NC_INTERNAL_BASE_URL'] = blank
            self.addCleanup(self._clear_env)
            self.assertEqual(self.Tenant._config_sync_base_url(),
                             'http://param-host:8069')

    def test_the_default_survives_with_neither_set(self):
        self._clear_env()
        self.env['ir.config_parameter'].sudo().search(
            [('key', '=', 'ncollection_saas.internal_base_url')]).unlink()
        self.assertEqual(self.Tenant._config_sync_base_url(),
                         'http://localhost:8069')

    # The other half of this fix — that the RUNNER is actually given the
    # variable — is asserted by scripts/ci/invariants.py's SaaS-stack rule, not
    # here: the odoo container mounts only custom_addons/ and oca/, so an Odoo
    # test physically cannot read docker-compose.saas.yml (the same reason
    # ci.yml runs the role-matrix guard on the host).
