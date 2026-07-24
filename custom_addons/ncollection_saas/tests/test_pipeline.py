# -*- coding: utf-8 -*-
"""Auto-provisioning pipeline (P2-T02) — CI-safe unit tests.

Cover the pipeline logic without spawning odoo subprocesses or creating real
databases: DB-name generation, the activation trigger + idempotency, the
success outcomes (tenant active, portal_url, welcome email queued), the
ownership-scoped rollback, and the status/retry guards. The full
create->login->rollback path is proven locally by
scripts/provisioning/verify_provisioning.sh (evidence in the PR).
"""
from unittest.mock import patch

from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged

ENGINE = 'odoo.addons.ncollection_saas.models.provisioning_job.ProvisioningJob'


@tagged('post_install', '-at_install')
class TestAutoProvisioningPipeline(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['ir.config_parameter'].sudo().set_param(
            'ncollection_saas.provisioning_quota_per_hour', '0')  # never block in tests
        cls.plan = cls.env['ncollection.subscription.plan'].create({
            'name': 'Growth', 'code': 'GROWTH',
            'allowed_module_names': 'crm, sale', 'max_users': 10,
        })

    def _make_tenant(self, name='Al Barari Trading', **kw):
        vals = {'company_name': name, 'email': 'owner@example.com',
                'plan_id': self.plan.id, 'status': 'trial'}
        vals.update(kw)
        return self.env['ncollection.tenant'].create(vals)

    def _make_subscription(self, tenant, status='draft'):
        return self.env['ncollection.subscription'].create({
            'name': 'SUB', 'tenant_id': tenant.id, 'plan_id': self.plan.id,
            'status': status,
        })

    # ---- DB-name generation (point 1) ------------------------------------

    def test_slugify_is_routable(self):
        Tenant = self.env['ncollection.tenant']
        self.assertEqual(Tenant._slugify_db_name('Al Barari Trading'), 'albararitrading')
        self.assertEqual(Tenant._slugify_db_name('  Açme, Inc.  '), 'acmeinc')
        # must start with a letter
        self.assertTrue(Tenant._slugify_db_name('7-Eleven').startswith('nc'))
        # too short gets padded
        self.assertGreaterEqual(len(Tenant._slugify_db_name('X')), 3)
        # never emits an underscore or hyphen (routability: subdomain == db)
        for probe in ('a_b', 'a-b-c', 'Foo Bar_Baz'):
            self.assertNotIn('_', Tenant._slugify_db_name(probe))
            self.assertNotIn('-', Tenant._slugify_db_name(probe))

    def test_generate_deduplicates_and_avoids_blocklist(self):
        tenant = self._make_tenant('Acme')
        with patch('%s._database_exists' % ENGINE, return_value=False):
            # 'acme' is free -> taken as-is
            self.assertEqual(tenant._generate_database_name('Acme'), 'acme')
            # reserved/fixture names are skipped with a numeric suffix
            self.assertEqual(tenant._generate_database_name('postgres'), 'postgres2')
            self.assertEqual(tenant._generate_database_name('provclient'), 'provclient2')
        # a live-cluster collision also bumps the suffix
        with patch('%s._database_exists' % ENGINE,
                   side_effect=lambda db: db == 'acme'):
            self.assertEqual(tenant._generate_database_name('Acme'), 'acme2')

    def test_validate_db_name_rejects_underscore(self):
        """#211: a subdomain label can't contain an underscore (invalid in a
        hostname), so an underscore name is unroutable under db_filter=^%d$ —
        provisioning must reject it at validation, not create an unreachable DB."""
        tenant = self._make_tenant('Underscore Co')
        job = self.env['ncollection.provisioning.job'].create({
            'tenant_id': tenant.id, 'database_name': 'bad_name'})
        for bad in ('bad_name', 'Upper', 'a-b', 'ab', '1abc'):
            with self.assertRaises(ValidationError):
                job._validate_db_name(bad)
        # a clean alphanumeric name passes the format + reserved gates
        with patch('%s._database_exists' % ENGINE, return_value=False):
            job._validate_db_name('cleanname')  # must not raise

    def test_cleanup_sweeps_legacy_underscore_db(self):
        """#214-H: DB_NAME_RE was tightened (no underscore) for validation, but the
        rollback/retry cleanup must still be able to DROP a legacy underscore-named
        zombie DB created before the tighten — otherwise it would leak forever."""
        from ..models import provisioning_job as pj
        self.assertIsNone(pj.DB_NAME_RE.match('legacy_name'))       # validator rejects it
        self.assertTrue(pj._CLEANUP_NAME_RE.match('legacy_name'))   # cleanup still accepts it
        tenant = self._make_tenant('Legacy Co')
        job = self.env['ncollection.provisioning.job'].create({
            'tenant_id': tenant.id, 'database_name': 'legacy_name'})
        with patch('%s._database_exists' % ENGINE, return_value=True), \
                patch('%s._drop_database' % ENGINE) as drop:
            job._rollback('legacy_name', created=True)
        drop.assert_called_once_with('legacy_name')
        # ...but the destructive cleanup NEVER targets the platform DB or a reserved
        # name, even though both pass the name-format check — the retry-sweep runs
        # before _validate_db_name, so this exclusion is the real guard (#214-H).
        self.assertFalse(job._safe_to_drop(self.env.cr.dbname))  # the platform DB
        self.assertFalse(job._safe_to_drop('postgres'))          # a reserved name

    def test_retry_sweep_never_drops_platform_or_reserved_db(self):
        """#214-H (CRITICAL): the retry-sweep runs BEFORE _validate_db_name, so a
        FAILED job whose database_name is the platform DB must never let a retry
        drop it. Exercises the real run path, not just _safe_to_drop in isolation."""
        tenant = self._make_tenant('Platform Collision Co')
        job = self.env['ncollection.provisioning.job'].create({
            'tenant_id': tenant.id, 'database_name': self.env.cr.dbname, 'status': 'failed'})
        with patch('%s._database_exists' % ENGINE, return_value=True), \
                patch('%s._drop_database' % ENGINE) as drop:
            # run_provisioning catches + persists the ValidationError (never re-raises),
            # so this returns normally; the point is the sweep never dropped anything.
            job.action_run_sync()
        drop.assert_not_called()

    def test_ready_tenant_database_name_is_immutable(self):
        """#214-G: the ORM mirror of the form readonly. A provisioned (ready) tenant's
        database_name cannot be changed via write/RPC (it is the live DB + subdomain);
        a not_provisioned tenant's still can (the generate/heal path)."""
        ready = self._make_tenant('Immutable Co', database_name='immutableco',
                                  database_status='ready')
        with self.assertRaises(ValidationError):
            ready.database_name = 'renamed'
        draft = self._make_tenant('Draft Co', database_name='draftco')  # not_provisioned
        draft.database_name = 'draftco2'  # allowed while not provisioned
        self.assertEqual(draft.database_name, 'draftco2')

    def test_ensure_database_name_idempotent(self):
        tenant = self._make_tenant('Acme', database_name='acme')
        with patch('%s._database_exists' % ENGINE, return_value=False):
            self.assertEqual(tenant._ensure_database_name(), 'acme')  # kept
        # an unroutable existing value is replaced
        tenant.database_name = 'Bad_Name'
        with patch('%s._database_exists' % ENGINE, return_value=False):
            new = tenant._ensure_database_name()
        self.assertTrue(new and '_' not in new)

    # ---- activation trigger + idempotency (point 2, 5) -------------------

    def test_activation_creates_and_enqueues_one_job(self):
        tenant = self._make_tenant()
        sub = self._make_subscription(tenant)
        Job = self.env['ncollection.provisioning.job']
        before = self.env['queue.job'].search_count([])
        with patch('%s._database_exists' % ENGINE, return_value=False):
            sub.action_activate()
        self.assertEqual(sub.status, 'active')
        jobs = Job.search([('tenant_id', '=', tenant.id)])
        self.assertEqual(len(jobs), 1, "exactly one provisioning job")
        self.assertTrue(jobs.database_name and '_' not in jobs.database_name)
        self.assertEqual(self.env['queue.job'].search_count([]), before + 1,
                         "enqueued exactly one queue.job")

    def test_activation_is_idempotent(self):
        tenant = self._make_tenant()
        sub = self._make_subscription(tenant)
        Job = self.env['ncollection.provisioning.job']
        with patch('%s._database_exists' % ENGINE, return_value=False):
            sub.action_activate()
            # a second trigger (e.g. re-run) must not create a duplicate job
            sub._trigger_provisioning()
        self.assertEqual(Job.search_count([('tenant_id', '=', tenant.id)]), 1)

    def test_no_trigger_for_already_provisioned_tenant(self):
        tenant = self._make_tenant(database_status='ready')
        sub = self._make_subscription(tenant)
        Job = self.env['ncollection.provisioning.job']
        with patch('%s._database_exists' % ENGINE, return_value=False):
            sub.action_activate()
        self.assertEqual(Job.search_count([('tenant_id', '=', tenant.id)]), 0)

    # ---- success outcomes (point 3) --------------------------------------

    def test_mark_done_sets_active_portal_and_welcome_mail(self):
        tenant = self._make_tenant()
        job = self.env['ncollection.provisioning.job'].create({
            'tenant_id': tenant.id, 'database_name': 'albararitrading'})
        mails_before = self.env['mail.mail'].search_count([])
        job._mark_done(setup_url='http://albararitrading.localhost:8069/web/reset?token=x')
        self.assertEqual(job.status, 'done')
        self.assertEqual(tenant.database_status, 'ready')
        self.assertEqual(tenant.status, 'active', "tenant activated on success")
        self.assertEqual(tenant.portal_url, 'http://albararitrading.localhost:8069')
        self.assertGreater(self.env['mail.mail'].search_count([]), mails_before,
                           "a welcome email was queued")

    # ---- safety: rollback ownership + status guard (point 5) -------------

    def test_rollback_only_drops_owned_db(self):
        job = self.env['ncollection.provisioning.job'].create({
            'tenant_id': self._make_tenant().id, 'database_name': 'acme'})
        with patch('%s._drop_database' % ENGINE) as drop:
            job._rollback('acme', created=False)  # pre-existing DB -> never drop
            drop.assert_not_called()
        with patch('%s._database_exists' % ENGINE, return_value=True), \
                patch('%s._drop_database' % ENGINE) as drop:
            job._rollback('acme', created=True)   # our own half-DB -> drop
            drop.assert_called_once()

    def test_done_job_refuses_rerun(self):
        job = self.env['ncollection.provisioning.job'].create({
            'tenant_id': self._make_tenant().id, 'database_name': 'acme', 'status': 'done'})
        with self.assertRaises(UserError):
            job.run_provisioning()

    def test_platform_db_name_is_reserved(self):
        from odoo.exceptions import ValidationError
        job = self.env['ncollection.provisioning.job'].create({
            'tenant_id': self._make_tenant().id, 'database_name': 'x'})
        with self.assertRaises(ValidationError):
            job._validate_db_name(self.env.cr.dbname)

    def test_failure_persists_status_and_alerts(self):
        tenant = self._make_tenant()
        job = self.env['ncollection.provisioning.job'].create({
            'tenant_id': tenant.id, 'database_name': 'acme', 'status': 'queued'})
        # engine fails at init; failure handling must record 'failed' + alert
        with patch('%s._validate_db_name' % ENGINE), \
                patch('%s._module_list' % ENGINE, return_value=['base']), \
                patch('%s._run_odoo_init' % ENGINE, side_effect=UserError('boom')), \
                patch('%s._drop_database' % ENGINE), \
                patch('%s._database_exists' % ENGINE, return_value=True):
            # records the failure and returns falsy (no re-raise, so the
            # failure state survives the transaction)
            self.assertFalse(job.run_provisioning())
        self.assertEqual(job.status, 'failed')
        self.assertEqual(tenant.database_status, 'error')
        self.assertIn('boom', job.log)
        # admin alert posted to the tenant chatter
        self.assertTrue(tenant.message_ids.filtered(
            lambda m: 'FAILED' in (m.body or '')))
