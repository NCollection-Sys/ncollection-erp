# -*- coding: utf-8 -*-
"""Public registration -> administrator approval -> provisioning.

The contract these tests pin:

  * a registration is a PENDING REQUEST: no database, no subscription activity,
    no provisioning job until an administrator approves;
  * the login is a USERNAME and can never be an email address;
  * the customer's password is stored only as a one-way hash that the tenant's
    own password context can verify, and is erased with the rest once applied;
  * one button serves every state, and pressing it twice (or retrying after a
    failure) never creates a second job, database or user.

Nothing here spawns an odoo subprocess or touches a real database: the engine's
`action_run` / subprocess runner are patched, exactly as test_checkout does.
"""
import json
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import HttpCase, TransactionCase, tagged

JOB = 'odoo.addons.ncollection_saas.models.provisioning_job.ProvisioningJob'
GOOD = {'username': 'atlasadmin', 'password': 'S3cure-pass!',
        'password_confirm': 'S3cure-pass!'}


@tagged('post_install', '-at_install')
class TestRegistrationEndpoint(HttpCase):

    def setUp(self):
        super().setUp()
        self.plan = self.env['ncollection.subscription.plan'].create({
            'name': 'Starter', 'code': 'REGSTARTER', 'monthly_price': 49,
            'yearly_price': 490, 'max_users': 5,
        })
        self.Tenant = self.env['ncollection.tenant'].sudo()

    def _register(self, **override):
        payload = {
            'company': 'Atlas Trading LLC', 'contact': 'Sam',
            'email': 'customer@example.com', 'subdomain': 'atlastrading',
            'plan': 'REGSTARTER', 'cycle': 'monthly', **GOOD}
        payload.update(override)
        res = self.url_open('/nc/checkout/register', data=json.dumps(
            {'jsonrpc': '2.0', 'method': 'call', 'params': payload}),
            headers={'Content-Type': 'application/json'})
        self.assertEqual(res.status_code, 200)
        return res.json()['result']

    # ------------------------------------------------------------ the request
    def test_registration_creates_a_pending_request_and_provisions_nothing(self):
        before = self.env['ncollection.provisioning.job'].sudo().search_count([])
        with patch('%s.action_run' % JOB) as run:
            result = self._register()
        self.assertTrue(result.get('success'), result)
        self.assertEqual(result['state'], 'pending')
        tenant = self.Tenant.search([('subdomain', '=', 'atlastrading')])
        self.assertEqual(tenant.registration_state, 'pending')
        self.assertEqual(tenant.provision_stage, 'pending')
        self.assertEqual(tenant.database_status, 'not_provisioned')
        self.assertEqual(tenant.subscription_id.status, 'draft')
        # THE POINT: no database is created before approval — not even a job.
        run.assert_not_called()
        self.assertEqual(
            self.env['ncollection.provisioning.job'].sudo().search_count([]), before)

    def test_the_login_is_the_username_and_the_email_is_only_contact_data(self):
        self._register()
        tenant = self.Tenant.search([('subdomain', '=', 'atlastrading')])
        self.assertEqual(tenant.admin_login, 'atlasadmin')
        self.assertEqual(tenant.email, 'customer@example.com')
        self.assertEqual(tenant._nc_admin_login(), 'atlasadmin')
        self.assertNotEqual(tenant._nc_admin_login(), tenant.email)

    def test_the_password_is_stored_only_as_a_hash_the_tenant_can_verify(self):
        self._register()
        tenant = self.Tenant.search([('subdomain', '=', 'atlastrading')])
        self.assertFalse(tenant.admin_password, "plaintext must never be stored")
        stored = tenant.admin_password_hash
        self.assertTrue(stored)
        self.assertNotIn(GOOD['password'], stored)
        ctx = self.env['res.users']._crypt_context()
        # What the tenant's own res.users will do at login.
        self.assertTrue(ctx.verify(GOOD['password'], stored))
        self.assertFalse(ctx.verify('not-the-password', stored))
        self.assertEqual(tenant.admin_credentials_state, 'pending')

    # ------------------------------------------------------- username rules
    def test_an_email_address_is_refused_as_the_username(self):
        for bad in ('customer@example.com', 'a@b'):
            res = self._register(username=bad)
            self.assertFalse(res['success'], bad)
            self.assertEqual(res['error'], 'username_is_email')
        self.assertFalse(self.Tenant.search([('subdomain', '=', 'atlastrading')]))

    def test_a_malformed_username_is_refused(self):
        for bad in ('ab', '1abc', 'has space', 'x' * 33, 'bad/slash'):
            res = self._register(username=bad)
            self.assertFalse(res['success'], bad)
            self.assertEqual(res['error'], 'invalid_username', bad)

    def test_the_username_is_required(self):
        res = self._register(username='')
        self.assertEqual(res['error'], 'missing_fields')

    # ------------------------------------------------------- password rules
    def test_the_two_passwords_must_match(self):
        res = self._register(password_confirm='S3cure-pass?')
        self.assertEqual(res['error'], 'password_mismatch')

    def test_a_short_password_is_refused(self):
        res = self._register(password='short', password_confirm='short')
        self.assertEqual(res['error'], 'password_too_short')

    def test_the_password_cannot_equal_the_username(self):
        same = GOOD['username']  # a password identical to the username
        res = self._register(password=same, password_confirm=same)
        self.assertEqual(res['error'], 'password_same_as_username')

    # ----------------------------------------------------------- subdomain
    def test_a_pending_request_holds_its_subdomain(self):
        self.assertTrue(self._register()['success'])
        again = self._register(email='other@example.com')
        self.assertFalse(again['success'])
        self.assertEqual(again['error'], 'subdomain_taken')
        self.assertEqual(self.Tenant.search_count(
            [('subdomain', '=', 'atlastrading')]), 1,
            "a second request for the same subdomain must not create a tenant")

    def test_a_reserved_or_invalid_subdomain_is_refused(self):
        self.assertEqual(self._register(subdomain='admin')['error'], 'subdomain_reserved')
        self.assertEqual(self._register(subdomain='Bad_Name')['error'], 'subdomain_invalid')

    def test_status_endpoint_reports_the_stage_without_leaking_anything(self):
        self._register()
        tenant = self.Tenant.search([('subdomain', '=', 'atlastrading')])
        res = self.url_open('/nc/checkout/status', data=json.dumps(
            {'jsonrpc': '2.0', 'method': 'call',
             'params': {'tenant_uuid': tenant.tenant_uuid}}),
            headers={'Content-Type': 'application/json'}).json()['result']
        self.assertEqual(res['stage'], 'pending')
        self.assertNotIn('login_url', res)
        for secret in ('admin_password_hash', 'admin_login', 'password'):
            self.assertNotIn(secret, json.dumps(res))


@tagged('post_install', '-at_install')
class TestApprovalGate(TransactionCase):
    """The administrator side: the gate, the one-click action, idempotency."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Tenant = cls.env['ncollection.tenant']
        cls.Job = cls.env['ncollection.provisioning.job']
        cls.plan = cls.env['ncollection.subscription.plan'].create({
            'name': 'Gate', 'code': 'GATEPLAN', 'max_users': 5})

    def _tenant(self, name='gateco', pending=True, login='gateadmin', **kw):
        vals = {
            'company_name': name.title(), 'database_name': name,
            'subdomain': name, 'plan_id': self.plan.id, 'email': name + '@x.example',
            'registration_state': 'pending' if pending else 'approved'}
        if login:
            vals['admin_login'] = login
        vals.update(kw)
        tenant = self.Tenant.create(vals)
        sub = self.env['ncollection.subscription'].create({
            'tenant_id': tenant.id, 'plan_id': self.plan.id, 'status': 'draft'})
        tenant.subscription_id = sub.id
        return tenant

    # --------------------------------------------------- pending expiry (#reservation)
    def _age(self, tenant, days):
        self.env.cr.execute(
            "UPDATE ncollection_tenant SET registration_date = now() - %s * interval '1 day' "
            "WHERE id = %s", (days, tenant.id))
        tenant.invalidate_recordset(['registration_date'])

    def test_an_undecided_request_expires_and_releases_its_subdomain(self):
        stale = self._tenant(name='stalerq')
        self._age(stale, 45)
        self.assertEqual(self.Tenant._cron_expire_pending_registrations(), 1)
        self.assertEqual(stale.registration_state, 'rejected')
        self.assertFalse(stale.subdomain)
        self.assertFalse(stale.database_name)
        self.assertFalse(stale.admin_password_hash)
        self.assertIn('stalerq', stale.registration_decision_note)   # what they asked for is kept
        # the name is free again for a new request
        self.assertTrue(self._tenant(name='stalerq', login='newadmin'))

    def test_recent_approved_and_built_requests_are_left_alone(self):
        recent = self._tenant(name='recentrq')
        self._age(recent, 3)
        approved = self._tenant(name='approvedrq', pending=False)
        self._age(approved, 90)
        self.assertEqual(self.Tenant._cron_expire_pending_registrations(), 0)
        self.assertEqual(recent.registration_state, 'pending')
        self.assertEqual(approved.registration_state, 'approved')

    def test_expiry_can_be_switched_off(self):
        stale = self._tenant(name='offrq')
        self._age(stale, 400)
        self.env['ir.config_parameter'].sudo().set_param(
            'ncollection_saas.pending_registration_days', '0')
        self.assertEqual(self.Tenant._cron_expire_pending_registrations(), 0)
        self.assertEqual(stale.registration_state, 'pending')

    def test_expiry_creates_no_database_and_no_job(self):
        stale = self._tenant(name='nodbrq')
        self._age(stale, 45)
        with patch('%s.action_run' % JOB) as run:
            self.Tenant._cron_expire_pending_registrations()
        run.assert_not_called()
        self.assertFalse(self.Job.search([('tenant_id', '=', stale.id)]))

    def test_manual_reject_and_expiry_are_the_same_path(self):
        a = self._tenant(name='pathrqa')
        b = self._tenant(name='pathrqb', login='pathb')
        self._age(b, 45)
        a.action_reject()
        self.Tenant._cron_expire_pending_registrations()
        for t in (a, b):
            self.assertEqual((t.registration_state, bool(t.subdomain), bool(t.database_name)),
                             ('rejected', False, False))

    # ------------------------------------------------------------ the gate
    def test_the_trigger_creates_no_job_for_a_pending_tenant(self):
        tenant = self._tenant()
        with patch('%s.action_run' % JOB) as run:
            tenant.subscription_id._trigger_provisioning()
        run.assert_not_called()
        self.assertFalse(self.Job.search([('tenant_id', '=', tenant.id)]))

    def test_the_engine_itself_refuses_an_unapproved_tenant(self):
        """Defence in depth: even a job someone created by hand cannot build a
        database for a request nobody approved."""
        tenant = self._tenant()
        job = self.Job.create({'tenant_id': tenant.id, 'database_name': 'gateco'})
        with patch.object(type(job), '_run_odoo_init') as init, \
                self.assertRaises(UserError):
            job.run_provisioning()
        init.assert_not_called()
        self.assertEqual(job.status, 'queued', "an unapproved request is not a failure")

    # --------------------------------------------------------- one click
    def test_approve_and_provision_is_one_action_and_creates_one_job(self):
        tenant = self._tenant()
        with patch('%s.action_run' % JOB) as run:
            tenant.action_provision()
        self.assertEqual(tenant.registration_state, 'approved')
        jobs = self.Job.search([('tenant_id', '=', tenant.id)])
        self.assertEqual(len(jobs), 1)
        run.assert_called_once()

    def test_approval_never_starts_an_already_expired_trial(self):
        """Every seeded plan has trial_days = 0, and start_trial would stamp
        'ends today' — which the daily sweep expires. The trial must be a real
        one, and it must start at approval, not when the request was filed."""
        self.assertFalse(self.plan.trial_days, "control: the plan has no trial_days")
        tenant = self._tenant(name='trialco')
        tenant.trial_end_date = '2020-01-01'          # filed long ago
        with patch('%s.action_run' % JOB):
            tenant.action_provision()
        today = fields.Date.context_today(tenant)
        sub = tenant.subscription_id
        self.assertEqual(sub.status, 'trial')
        self.assertGreater(sub.trial_end_date, today)
        self.assertEqual(tenant.trial_end_date, sub.trial_end_date,
                         "the trial clock restarts at approval")
        self.assertFalse(sub._process_trial_expiry(),
                         "the sweep must not expire a customer on approval day")

    def test_pressing_it_again_never_creates_a_second_job(self):
        tenant = self._tenant()
        with patch('%s.action_run' % JOB) as run:
            tenant.action_provision()
            tenant.action_provision()
            tenant.action_provision()
        self.assertEqual(
            self.Job.search_count([('tenant_id', '=', tenant.id)]), 1)
        self.assertEqual(run.call_count, 1, "the engine must be started once")

    def test_an_admin_created_tenant_is_provisioned_with_the_same_action(self):
        tenant = self._tenant(name='adminmade', pending=False)
        with patch('%s.action_run' % JOB) as run:
            tenant.action_provision()
        self.assertEqual(self.Job.search_count([('tenant_id', '=', tenant.id)]), 1)
        run.assert_called_once()

    def test_a_ready_tenant_is_never_provisioned_again(self):
        tenant = self._tenant(name='alreadyup', pending=False)
        tenant.write({'database_status': 'ready'})
        with patch('%s.action_run' % JOB) as run:
            note = tenant._nc_provision_one()
        run.assert_not_called()
        self.assertIn('already provisioned', note)
        self.assertFalse(self.Job.search([('tenant_id', '=', tenant.id)]))

    # --------------------------------------------------------- failure/retry
    def test_retry_reruns_the_failed_job_instead_of_stacking_a_second(self):
        tenant = self._tenant(name='failco', pending=False)
        failed = self.Job.create({'tenant_id': tenant.id, 'database_name': 'failco',
                                  'status': 'failed'})
        tenant.write({'database_status': 'error'})
        sub = tenant.subscription_id
        sub.status = 'trial'
        with patch('%s.action_run' % JOB, autospec=True) as run:
            tenant.action_provision()
        self.assertEqual(self.Job.search_count([('tenant_id', '=', tenant.id)]), 1,
                         "no duplicate provisioning record on retry")
        self.assertEqual(run.call_args[0][0], failed, "the SAME job is re-run")

    def test_retry_shows_provisioning_again_instead_of_failed(self):
        tenant = self._tenant(name='retryvis', pending=False)
        job = self.Job.create({'tenant_id': tenant.id, 'database_name': 'retryvis',
                               'status': 'failed'})
        tenant.write({'database_status': 'error'})
        tenant.subscription_id.status = 'trial'
        self.assertEqual(tenant.provision_stage, 'failed')
        with patch('%s.action_run' % JOB):
            tenant.action_provision()
        self.assertEqual(job.status, 'queued')
        self.assertEqual(tenant.provision_stage, 'provisioning')

    def test_a_queued_job_already_reads_as_provisioning(self):
        tenant = self._tenant(name='queuedvis', pending=False)
        self.assertEqual(tenant.provision_stage, 'approved')
        self.Job.create({'tenant_id': tenant.id, 'database_name': 'queuedvis'})
        self.assertEqual(tenant.provision_stage, 'provisioning')

    # ------------------------------------------------- database ownership
    def _run_engine(self, job, exists):
        """Run the REAL engine with the database layer faked. `exists` is a
        one-element list holding whether the database currently exists; the
        faked drop flips it, the faked create sets it."""
        dropped = []

        def fake_drop(_self, db):
            dropped.append(db)
            exists[0] = False

        def fake_init(_self, db, modules):
            exists[0] = True

        with patch.object(type(job), '_database_exists', new=lambda _s, db: exists[0]), \
                patch.object(type(job), '_drop_database', new=fake_drop), \
                patch.object(type(job), '_run_odoo_init', new=fake_init), \
                patch.object(type(job), '_module_list', return_value=['base']), \
                patch.object(type(job), '_verify_localization'), \
                patch.object(type(job), '_seed_tenant', return_value=None):
            ok = job.run_provisioning()
        return ok, dropped

    def test_a_foreign_database_is_never_dropped_on_a_retry(self):
        """THE DATA-LOSS GUARD. A retry used to clear any database carrying the
        tenant's name. If that database is somebody else's — a live tenant, a
        restore, a collision — the retry destroyed it. Now it never does, however
        many times it is pressed."""
        tenant = self._tenant(name='foreignco', pending=False)
        job = self.Job.create({'tenant_id': tenant.id, 'database_name': 'foreignco',
                               'status': 'failed'})
        exists = [True]            # somebody else's database holds the name
        for _attempt in range(3):
            ok, dropped = self._run_engine(job, exists)
            self.assertFalse(ok)
            self.assertEqual(dropped, [], "a foreign database must never be dropped")
            self.assertTrue(exists[0])
            self.assertIn('already exists', job.log)

    def test_a_database_this_job_began_building_is_cleared_and_rebuilt(self):
        """The killed-worker case: the job is re-queued and finds the database its
        own interrupted attempt left. It clears and rebuilds it — it does not fail
        on its own leftovers, and it does not build on a half-installed database."""
        tenant = self._tenant(name='zombieco', pending=False)
        job = self.Job.create({'tenant_id': tenant.id, 'database_name': 'zombieco'})
        job._nc_mark_db_owned('zombieco')       # the interrupted attempt's record
        exists = [True]
        ok, dropped = self._run_engine(job, exists)
        self.assertTrue(ok, job.log)
        self.assertEqual(dropped, ['zombieco'])
        self.assertEqual(job.status, 'done')
        self.assertEqual(tenant.database_status, 'ready')

    def test_the_ownership_record_belongs_to_one_job_only(self):
        tenant = self._tenant(name='ownedco', pending=False)
        job_a = self.Job.create({'tenant_id': tenant.id, 'database_name': 'ownedco'})
        job_b = self.Job.create({'tenant_id': tenant.id, 'database_name': 'ownedco'})
        job_a._nc_mark_db_owned('ownedco')
        self.assertTrue(job_a._nc_db_is_owned('ownedco'))
        self.assertFalse(job_b._nc_db_is_owned('ownedco'),
                         "another job must not inherit the right to drop it")

    def test_the_ownership_record_is_removed_when_the_job_finishes(self):
        tenant = self._tenant(name='tidyco', pending=False)
        job = self.Job.create({'tenant_id': tenant.id, 'database_name': 'tidyco'})
        exists = [False]
        ok, _dropped = self._run_engine(job, exists)
        self.assertTrue(ok, job.log)
        self.assertFalse(job._nc_db_is_owned('tidyco'),
                         "a finished database must not stay marked as droppable")

    def test_failure_after_creation_rolls_back_only_the_owned_database(self):
        tenant = self._tenant(name='rollco', pending=False)
        job = self.Job.create({'tenant_id': tenant.id, 'database_name': 'rollco'})
        exists = [False]
        dropped = []

        def fake_init_then_fail(_self, db, modules):
            exists[0] = True
            raise UserError('install failed')

        def fake_drop(_self, db):
            dropped.append(db)
            exists[0] = False

        with patch.object(type(job), '_database_exists', new=lambda _s, db: exists[0]), \
                patch.object(type(job), '_drop_database', new=fake_drop), \
                patch.object(type(job), '_run_odoo_init', new=fake_init_then_fail), \
                patch.object(type(job), '_module_list', return_value=['base']):
            ok = job.run_provisioning()
        self.assertFalse(ok)
        self.assertEqual(dropped, ['rollco'], "the half-built database is rolled back")
        self.assertFalse(job._nc_db_is_owned('rollco'))
        self.assertEqual(job.status, 'failed')
        self.assertEqual(tenant.provision_stage, 'failed')

    def test_failure_is_visible_as_a_stage_not_a_false_ready(self):
        tenant = self._tenant(name='visibly', pending=False)
        tenant.write({'database_status': 'error'})
        self.assertEqual(tenant.provision_stage, 'failed')
        tenant.write({'database_status': 'provisioning'})
        self.assertEqual(tenant.provision_stage, 'provisioning')
        tenant.write({'database_status': 'ready'})
        self.assertEqual(tenant.provision_stage, 'ready')

    # ------------------------------------------------------------- rejection
    def test_a_rejected_request_cannot_be_provisioned(self):
        tenant = self._tenant(name='nopeco')
        tenant.action_reject()
        self.assertEqual(tenant.provision_stage, 'rejected')
        with self.assertRaises(UserError):
            tenant.action_provision()

    def test_rejecting_releases_the_subdomain_and_erases_the_password_hash(self):
        tenant = self._tenant(name='releaseme', admin_password_hash='$pbkdf2$x')
        tenant.registration_decision_note = 'duplicate'
        tenant.action_reject()
        self.assertFalse(tenant.subdomain)
        self.assertFalse(tenant.database_name)
        self.assertFalse(tenant.admin_password_hash)
        self.assertIn('releaseme', tenant.registration_decision_note,
                      "what they asked for is kept in the note")
        ok, _reason = self.Tenant._nc_subdomain_availability('releaseme', deep=False)
        self.assertTrue(ok, "a rejected request must not hold its name hostage")

    def test_a_workspace_that_exists_cannot_be_rejected(self):
        tenant = self._tenant(name='liveone', pending=False)
        tenant.write({'database_status': 'ready'})
        with self.assertRaises(UserError):
            tenant.action_reject()

    # ------------------------------------------------------------- subdomain
    def test_the_subdomain_decides_the_database_name(self):
        """db_filter=^%d$ routes <label>.<base> to the database named <label>.
        A name generated from the company would leave the URL pointing at
        nothing."""
        tenant = self.Tenant.create({
            'company_name': 'Some Very Long Company Name LLC',
            'subdomain': 'shortname', 'plan_id': self.plan.id})
        self.assertEqual(tenant._ensure_database_name(), 'shortname')
        self.assertEqual(tenant.database_name, 'shortname')
        self.assertTrue(tenant.tenant_url.startswith('https://shortname.'))

    def test_with_no_subdomain_a_name_is_still_generated(self):
        tenant = self.Tenant.create({
            'company_name': 'Generated Name Co', 'plan_id': self.plan.id})
        self.assertRegex(tenant._ensure_database_name(), r'^[a-z][a-z0-9]{2,62}$')

    # ------------------------------------------------------------- username
    def test_provisioning_refuses_a_tenant_with_no_username(self):
        """Never guess the login from the email."""
        tenant = self._tenant(name='nologin', pending=False, login=False)
        with patch('%s.action_run' % JOB) as run, self.assertRaises(ValidationError):
            tenant.action_provision()
        run.assert_not_called()
        self.assertFalse(self.Job.search([('tenant_id', '=', tenant.id)]))

    def test_the_engine_checks_the_username_before_creating_anything(self):
        tenant = self._tenant(name='enginelogin', pending=False, login=False)
        job = self.Job.create({'tenant_id': tenant.id, 'database_name': 'enginelogin'})
        with patch.object(type(job), '_run_odoo_init') as init, \
                patch.object(type(job), '_database_exists', return_value=False):
            ok = job.run_provisioning()
        self.assertFalse(ok)
        init.assert_not_called()
        self.assertEqual(job.status, 'failed')
        self.assertIn('username', (job.log or '').lower())
        self.assertEqual(tenant.provision_stage, 'failed')

    # ------------------------------------------------------------- the seed
    def test_the_seed_receives_the_username_and_the_hash_never_the_plaintext(self):
        tenant = self._tenant(name='seedco', pending=False,
                              admin_password_hash='$pbkdf2-sha512$hash')
        job = self.Job.create({'tenant_id': tenant.id, 'database_name': 'seedco'})
        captured = {}

        def fake_run(self_, cmd, label, stdin=None, env=None, **kw):
            captured.update(env or {})
            return 'SEED_OK'

        # _run_odoo_subprocess lives on the subprocess mixin, merged into the
        # runtime class by _inherit, so patch the runtime class, not the import.
        with patch.object(type(job), '_run_odoo_subprocess', new=fake_run):
            job._seed_tenant('seedco')
        self.assertEqual(captured['NC_ADMIN_LOGIN'], 'gateadmin')
        self.assertEqual(captured['NC_ADMIN_PASSWORD_HASH'], '$pbkdf2-sha512$hash')
        self.assertEqual(captured['NC_ADMIN_PASSWORD'], '')
        self.assertNotEqual(captured['NC_ADMIN_LOGIN'], captured['NC_ADMIN_EMAIL'])

    def test_the_hash_is_erased_once_provisioning_succeeds(self):
        tenant = self._tenant(name='eraseco', pending=False,
                              admin_password_hash='$pbkdf2-sha512$hash')
        tenant._nc_clear_admin_password()
        self.assertFalse(tenant.admin_password_hash)

    # ------------------------------------------------------------- security
    def test_only_a_platform_administrator_can_approve_or_provision(self):
        tenant = self._tenant(name='guardedco')
        clerk = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Clerk', 'login': 'platformclerk',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        for action in ('action_approve', 'action_provision', 'action_reject'):
            # AccessError is a UserError, so this covers both refusals.
            with self.assertRaises(UserError, msg=action):
                getattr(tenant.with_user(clerk), action)()
        self.assertEqual(tenant.registration_state, 'pending')
        self.assertFalse(self.Job.search([('tenant_id', '=', tenant.id)]))
