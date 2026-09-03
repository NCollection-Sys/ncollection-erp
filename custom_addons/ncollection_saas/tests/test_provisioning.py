# -*- coding: utf-8 -*-
"""P2-T01 provisioning engine — CI-safe unit tests.

These cover the pure logic (validation, module set, quota, status transitions,
enqueue) WITHOUT spawning odoo subprocesses or creating real databases. The
full create->login->rollback path is proven locally (see
tests/test_provisioning_engine.py, tagged out of the default CI run, and the
evidence in the PR).
"""
from unittest.mock import patch

from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged

MODEL = 'ncollection.saas.provisioning'  # dotted path only used in messages
ENGINE = 'odoo.addons.ncollection_saas.models.provisioning_job.ProvisioningJob'


@tagged('post_install', '-at_install')
class TestProvisioningLogic(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Job = cls.env['ncollection.provisioning.job']
        cls.plan = cls.env['ncollection.subscription.plan'].create({
            'name': 'Test Plan', 'code': 'TEST',
            'allowed_module_names': 'crm, sale, crm',  # dup on purpose
            'max_users': 5,
        })
        cls.tenant = cls.env['ncollection.tenant'].create({
            'company_name': 'Acme Co', 'database_name': 'acme',
            'email': 'owner@acme.example', 'plan_id': cls.plan.id, 'status': 'trial',
        })
        cls.job = cls.env['ncollection.provisioning.job'].create({
            'tenant_id': cls.tenant.id, 'database_name': 'acme', 'status': 'queued',
        })

    # ---- name validation (security) --------------------------------------

    def test_valid_name_passes(self):
        # patch the collision check so the result never depends on which DBs
        # happen to exist on the test cluster (deterministic in CI + locally).
        with patch('%s._database_exists' % ENGINE, return_value=False):
            self.job._validate_db_name('clienta')

    def test_reserved_names_rejected(self):
        for bad in ('admin', 'postgres', 'template1', 'www', 'api', 'staging'):
            with self.assertRaises(ValidationError, msg="'%s' must be reserved" % bad):
                self.job._validate_db_name(bad)

    def test_malformed_names_rejected(self):
        for bad in ('Acme', 'a', 'ab', '1abc', 'a-b', 'a b', '../etc', 'drop;', ''):
            with self.assertRaises(ValidationError, msg="'%s' must be rejected" % bad):
                self.job._validate_db_name(bad)

    def test_collision_rejected(self):
        with patch('%s._database_exists' % ENGINE, return_value=True):
            with self.assertRaises(ValidationError):
                self.job._validate_db_name('clientb')

    # ---- module set ------------------------------------------------------

    def test_module_list_core_plus_plan_deduped(self):
        mods = self.job._module_list()
        self.assertEqual(mods[:3], ['base', 'ncollection_core', 'ncollection_branding'])
        self.assertIn('crm', mods)
        self.assertIn('sale', mods)
        self.assertEqual(mods.count('crm'), 1, "plan duplicates must be collapsed")

    # ---- quota (security) ------------------------------------------------

    def test_quota_blocks_over_limit(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'ncollection_saas.provisioning_quota_per_hour', '1')
        # setUpClass already made 1 job; add a 2nd this hour -> 2 > 1
        self.Job.create({'tenant_id': self.tenant.id, 'database_name': 'acme2'})
        with self.assertRaises(UserError):
            self.job._check_quota()

    def test_quota_zero_disables(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'ncollection_saas.provisioning_quota_per_hour', '0')
        self.job._check_quota()  # must not raise

    # ---- status transitions ----------------------------------------------

    def test_mark_done_transitions(self):
        self.tenant.onboarding_stage = 'signup'
        self.job._mark_done()
        self.assertEqual(self.job.status, 'done')
        self.assertTrue(self.job.completed_at)
        self.assertEqual(self.tenant.database_status, 'ready')
        self.assertEqual(self.tenant.onboarding_stage, 'setup')

    def test_mark_failed_transitions(self):
        self.job._mark_failed()
        self.assertEqual(self.job.status, 'failed')
        self.assertEqual(self.tenant.database_status, 'error')

    def test_log_appends(self):
        self.job.log = False
        self.job._append_log('first')
        self.job._append_log('second')
        self.assertIn('first', self.job.log)
        self.assertIn('second', self.job.log)

    # ---- enqueue (queue_job) ---------------------------------------------

    def test_action_run_enqueues_job(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'ncollection_saas.provisioning_quota_per_hour', '0')  # don't block
        before = self.env['queue.job'].search_count([])
        self.job.action_run()
        after = self.env['queue.job'].search_count([])
        self.assertEqual(after, before + 1, "action_run must enqueue one queue.job")

    def test_action_run_respects_quota(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'ncollection_saas.provisioning_quota_per_hour', '1')
        self.Job.create({'tenant_id': self.tenant.id, 'database_name': 'acme3'})
        with self.assertRaises(UserError):
            self.job.action_run()


@tagged('post_install', '-at_install')
class TestSeedTenantEnv(TransactionCase):
    """#451: an unset ``allowed_module_names`` (ORM ``False``, not ``''``) made
    it into the seed subprocess's ``env`` dict as a bare ``bool``. Every value
    in a subprocess ``env=`` mapping must be ``str`` -- CPython's ``os.fsencode``
    (used to encode it) raises ``TypeError: expected str, bytes or os.PathLike
    object, not bool`` otherwise, which is exactly what surfaced: the tenant DB
    was created and its core modules installed, then the run rolled back and
    dropped it the moment ``_seed_tenant()`` tried to spawn the seed subprocess.

    ``_run_odoo_subprocess`` is patched out entirely -- these tests assert on
    the ``env`` mapping a real call WOULD have received, never spawning a
    subprocess or touching a real database, matching this file's existing
    house style (see the module docstring)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Job = cls.env['ncollection.provisioning.job']
        cls.tenant = cls.env['ncollection.tenant'].create({
            'company_name': 'Seed Co', 'database_name': 'seedco',
            'email': 'owner@seedco.example', 'status': 'trial',
        })
        cls.job = cls.Job.create({
            'tenant_id': cls.tenant.id, 'database_name': 'seedco', 'status': 'queued',
        })

    def _seed_env(self):
        """Run _seed_tenant() with the subprocess call intercepted; return the
        env mapping it was given. Fails LOUDLY (TypeError) on the pre-fix code
        exactly as it did in production -- nothing here silences that class of
        bug, it only stops it from reaching a real subprocess."""
        captured = {}

        def fake_run(_self, cmd, label, stdin=None, env=None, timeout=None):
            captured['env'] = env
            return ''

        # _run_odoo_subprocess lives on ncollection.saas.subprocess.mixin,
        # combined onto the runtime model class through Odoo's own _inherit
        # merge rather than plain Python subclassing -- the imported
        # ProvisioningJob class object (ENGINE, used above for
        # _database_exists, which IS defined directly in this module) does
        # NOT carry it, so patch.object on the actual runtime class instead.
        with patch.object(type(self.job), '_run_odoo_subprocess', new=fake_run):
            self.job._seed_tenant('seedco')
        return captured['env']

    def test_an_unset_plan_module_list_does_not_crash_the_seed_subprocess(self):
        """THE BUG, PINNED. No plan at all -- the most common shape for a
        fresh trial tenant -- must not raise, and the env value it produces
        must be a real str (a bool would fail exactly as #451 did, further
        upstream, the moment a real subprocess.run() tried to encode it)."""
        self.tenant.plan_id = False
        env = self._seed_env()
        self.assertIsInstance(env['NC_ALLOWED_MODULES'], str)
        self.assertEqual(env['NC_ALLOWED_MODULES'], '')

    def test_a_plan_with_no_allowed_modules_set_does_not_crash_the_seed_subprocess(self):
        """THE EXACT #451 REPRODUCTION: a plan exists (unlike the case above)
        but its Text field was never filled in, so the ORM hands back False,
        not ''. This is the case that actually broke in production."""
        plan = self.env['ncollection.subscription.plan'].create({
            'name': 'Bare Plan', 'code': 'BARE', 'max_users': 1,
        })
        self.assertFalse(plan.allowed_module_names,
                         "an unset Text field must read as ORM False for this "
                         "test to actually exercise #451's failure mode")
        self.tenant.plan_id = plan.id
        env = self._seed_env()
        self.assertIsInstance(env['NC_ALLOWED_MODULES'], str)
        self.assertEqual(env['NC_ALLOWED_MODULES'], '')

    def test_a_populated_plan_still_projects_its_modules_into_the_seed_env(self):
        """The regression control the fix must not break: a plan that DOES
        list modules must still hand them to the seed script verbatim -- the
        coalesce added for #451 must not swallow real data."""
        plan = self.env['ncollection.subscription.plan'].create({
            'name': 'Full Plan', 'code': 'FULL',
            'allowed_module_names': 'crm,sale', 'max_users': 5,
        })
        self.tenant.plan_id = plan.id
        env = self._seed_env()
        self.assertEqual(env['NC_ALLOWED_MODULES'], 'crm,sale')
