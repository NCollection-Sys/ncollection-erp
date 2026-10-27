# -*- coding: utf-8 -*-
"""The platform side of the release gate: what it records, and what it refuses because of it.

The tenant's gate itself (the 503 instead of the 500) is tested in ncollection_core and, over HTTP against a stale
database, in scripts/upgrade/verify_upgrade.sh. Here the tenant's answer is given, and the platform must act on it:

* CASE E - Company Entry into a tenant that is not READY is refused before any token exists; no grant is pushed.
* CASE D - provisioning hands a tenant over only after its gate validated it; a failed validation rolls it back.
* The Fleet Migration line upgrades exactly what the tenant's gate names, and refuses a list that does not cover it.
"""
import os
from unittest.mock import MagicMock, patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.ncollection_core import release_gate as gate
from odoo.addons.ncollection_saas.models import config_sync
from odoo.addons.ncollection_saas.models.tenant_release import TenantRelease

PLATFORM_GROUP = 'ncollection_subscription.group_platform_admin'
ENTRY = 'odoo.addons.ncollection_saas.models.platform_entry'


def _status(state, behind=(), hold='', compatible=None, **extra):
    body = {'state': state, 'behind': [list(row) for row in behind], 'missing': [], 'transitional': [],
            'schema_gap': False, 'hold': hold, 'hold_reason': '', 'db_fingerprint': 'a' * 64,
            'release_fingerprint': 'b' * 64,
            'compatible': (state == 'ready' or bool(hold) and not behind) if compatible is None else compatible}
    body.update(extra)
    return 200, body


@tagged('post_install', '-at_install')
class TestReleaseCompat(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['ir.config_parameter'].sudo().set_param('ncollection_saas.base_domain', 'example.com')
        internal = cls.env.ref('base.group_user').id
        cls.alice = cls.env['res.users'].create({
            'name': 'Alice Admin', 'login': 'rel_alice',
            'group_ids': [(6, 0, [internal, cls.env.ref(PLATFORM_GROUP).id])]})
        cls.atlas = cls.env['ncollection.tenant'].with_user(cls.env.ref('base.user_root')).create({
            'company_name': 'Atlas', 'database_name': 'atlasrel', 'database_status': 'ready', 'status': 'active',
            'platform_admin_ids': [(6, 0, cls.alice.ids)]})

    # ------------------------------------------------------------------ CASE E: Company Entry
    def _enter(self, answer):
        post = MagicMock()
        with patch.object(TenantRelease, '_nc_release_call', return_value=answer), \
                patch.dict(os.environ, {'NC_CONFIG_SYNC_KEY': 'master-key-for-tests'}), \
                patch(ENTRY + '.requests.post', post):
            action = self.atlas.with_user(self.alice).action_enter_company()
        # A refusal is a warning notification, never a URL to the tenant (and never an exception, which would roll back
        # the reason recorded on the tenant form).
        self.assertEqual(action.get('tag'), 'display_notification', action)
        self.assertNotIn('url', action)
        return action['params']['message'], post

    def test_case_e_entry_into_a_tenant_behind_the_code_is_refused_and_issues_nothing(self):
        message, post = self._enter(_status('upgrade_required', behind=[
            ('ncollection_core', '19.0.1.18.0', '19.0.1.23.0'),
            ('ncollection_branding', '19.0.1.8.0', '19.0.1.12.0')]))
        self.assertFalse(post.called, 'no grant was pushed: no token exists that could lead to an HTTP 500')
        self.assertIn('not ready for the current release', message)
        self.assertIn('No entry was issued', message)
        self.assertEqual(self.atlas.nc_release_state, 'upgrade_required')
        self.assertIn('ncollection_core: database 19.0.1.18.0 < code 19.0.1.23.0', self.atlas.nc_release_detail,
                      'the operator sees exactly why')
        self.assertIn('ncollection_branding', self.atlas.nc_release_detail)

    def test_case_e_every_state_but_ready_is_refused(self):
        for answer in (_status('upgrading', hold='upgrading'), _status('upgrade_failed', hold='upgrade_failed'),
                       _status('blocked', hold='blocked'), _status('provisioning', hold='provisioning'),
                       (None, {'error': 'connection refused'}), (404, {})):
            with self.subTest(answer=answer[1].get('state', answer[0])):
                _message, post = self._enter(answer)
                self.assertFalse(post.called)
        self.assertEqual(self.atlas.nc_release_state, 'unknown', 'a 404 is recorded as unknown, not as ready')

    def test_an_unreachable_tenant_is_recorded_as_unreachable(self):
        self._enter((None, {'error': 'connection refused'}))
        self.assertEqual(self.atlas.nc_release_state, 'unreachable')

    def test_the_check_button_is_for_platform_staff_and_records_the_state(self):
        with patch.object(TenantRelease, '_nc_release_call', return_value=_status('ready')):
            self.atlas.with_user(self.alice).action_nc_release_check()
        self.assertEqual(self.atlas.nc_release_state, 'ready')
        self.assertTrue(self.atlas.nc_release_checked_at)
        clerk = self.env['res.users'].create({'name': 'Clerk', 'login': 'rel_clerk',
                                              'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        with self.assertRaises(Exception):
            self.atlas.with_user(clerk).action_nc_release_check()

    # ------------------------------------------------------------------ the Release section, per state
    def _record(self, answer, lang=None):
        with patch.object(TenantRelease, '_nc_release_call', return_value=answer):
            self.atlas.with_user(self.alice).with_context(lang=lang or self.env.lang).action_nc_release_check()
        return self.atlas

    def test_the_release_section_says_exactly_why_and_what_to_do_in_every_state(self):
        """Everything comes from the tenant's gate (answered before its registry loads), so it is there for exactly
        the tenants whose application cannot run: no tenant registry is needed to read it."""
        behind = [('ncollection_core', '19.0.1.18.0', '19.0.1.23.0'), ('ncollection_auth', '19.0.1.2.0', '19.0.1.3.0')]
        tenant = self._record(_status('upgrade_required', behind=behind,
                                      required=['ncollection_auth', 'ncollection_core']))
        self.assertEqual(tenant.nc_release_state, 'upgrade_required')
        self.assertEqual(tenant.nc_release_required, 'ncollection_auth,ncollection_core', 'the exact -u list')
        self.assertIn('ncollection_core: database 19.0.1.18.0 < code 19.0.1.23.0', tenant.nc_release_detail)
        self.assertEqual((tenant.nc_release_fingerprint, tenant.nc_release_code_fingerprint), ('a' * 64, 'b' * 64))
        self.assertIn('ncollection_auth, ncollection_core', tenant.nc_release_action)
        self.assertIn('Fleet Migration', tenant.nc_release_action)
        self.assertTrue(tenant.nc_release_checked_at)
        expected = {
            'upgrading': (dict(hold='upgrading'), 'being validated'),
            'upgrade_failed': (dict(hold='upgrade_failed', hold_reason='migration crashed',
                                    last_error='2026-10-24T06:00:00Z migration crashed'), 'last upgrade failed'),
            'provisioning': (dict(hold='provisioning'), 'being provisioned'),
        }
        for state, (extra, says) in expected.items():
            with self.subTest(state=state):
                self.assertEqual(self._record(_status(state, **extra)).nc_release_state, state)
                self.assertIn(says, tenant.nc_release_action)
        self.assertEqual(tenant.nc_release_last_error, '2026-10-24T06:00:00Z migration crashed')
        self._record(_status('blocked', missing=['ncollection_gone']))
        self.assertIn('not deployed with the current code: ncollection_gone', tenant.nc_release_action)
        self._record(_status('blocked', schema_gap=True))
        self.assertIn('lacks a table or column', tenant.nc_release_action)
        self.assertIn('schema: a table or column the code declares is missing', tenant.nc_release_detail)
        self._record(_status('blocked', hold='blocked', hold_reason='operator'))
        self.assertIn('An operator held this tenant', tenant.nc_release_action)
        self._record(_status('ready', last_ok='2026-10-24T07:00:00Z release=' + 'b' * 64,
                             last_error='2026-10-24T06:00:00Z migration crashed'))
        self.assertEqual(tenant.nc_release_state, 'ready')
        self.assertFalse(tenant.nc_release_action)
        self.assertEqual(str(tenant.nc_release_last_ok_at), '2026-10-24 07:00:00')
        self.assertFalse(tenant.nc_release_last_error, 'an error older than the last successful release is history')

    def test_the_required_action_follows_the_operator_s_language(self):
        self.env['res.lang']._activate_lang('ar_001')
        self.env['ir.module.module']._load_module_terms(['ncollection_saas'], ['ar_001'], overwrite=True)
        tenant = self._record(_status('upgrade_required', behind=[('ncollection_core', '19.0.1.1.0', '19.0.1.23.0')],
                                      required=['ncollection_core']), lang='ar_001')
        self.assertIn('ترحيل الأسطول', tenant.nc_release_action)
        self.assertIn('ncollection_core', tenant.nc_release_action)

    def test_the_release_section_is_on_the_tenant_form(self):
        arch = self.env['ncollection.tenant'].get_view(view_type='form')['arch']
        for name in ('nc_release_state', 'nc_release_action', 'nc_release_required', 'nc_release_detail',
                     'nc_release_code_fingerprint', 'nc_release_fingerprint', 'nc_release_checked_at',
                     'nc_release_last_ok_at', 'nc_release_last_error', 'action_nc_release_check'):
            self.assertIn('name="%s"' % name, arch)
        self.assertIn('o_nc_release_banner', arch)

    def test_a_refused_open_records_the_real_state_and_what_to_do(self):
        """/open answers 409 WITH the gate's full state when the code is still ahead: that is a diagnosis, not an
        'unknown' tenant whose code predates the gate."""
        status = _status('upgrade_required', behind=[('ncollection_core', '19.0.1.1.0', '19.0.1.23.0')],
                         required=['ncollection_core'])
        with patch.object(TenantRelease, '_nc_release_call', return_value=(409, status[1])):
            state = self.atlas._nc_release_open()
        self.assertEqual(state, 'upgrade_required')
        self.assertEqual(self.atlas.nc_release_required, 'ncollection_core')
        self.assertIn('ncollection_core', self.atlas.nc_release_action)
        self.assertNotIn('predates', self.atlas.nc_release_detail or '')

    def test_a_tenant_reply_is_read_only_up_to_the_cap(self):
        """The platform never buffers more of a tenant's answer than the cap (a misbehaving endpoint)."""
        import json as _json
        from ..models import tenant_release

        def reply(body):
            resp = MagicMock(status_code=200)
            resp.raw.read.side_effect = lambda n, decode_content=True: body[:n]
            return resp

        good = _json.dumps({'state': 'ready'}).encode()
        huge = b'{"state": "ready", "pad": "' + b'x' * (tenant_release._MAX_REPLY + 10) + b'"}'
        with patch.dict(os.environ, {'NC_CONFIG_SYNC_KEY': 'master-key-for-tests'}):
            with patch(tenant_release.__name__ + '.requests.get', return_value=reply(good)) as get:
                self.assertEqual(self.atlas._nc_release_call(tenant_release.STATUS_PATH), (200, {'state': 'ready'}))
            self.assertTrue(get.call_args.kwargs['stream'])
            with patch(tenant_release.__name__ + '.requests.get', return_value=reply(huge)) as get:
                self.assertEqual(self.atlas._nc_release_call(tenant_release.STATUS_PATH), (200, {}))
            self.assertEqual(get.return_value.raw.read.call_args.args[0], tenant_release._MAX_REPLY + 1)

    # ------------------------------------------------------------------ CASE D: provisioning
    def _job(self, db='freshrel'):
        tenant = self.env['ncollection.tenant'].create({
            'company_name': 'Fresh %s' % db, 'database_name': db, 'email': 'o@example.com', 'admin_login': 'owner'})
        return self.env['ncollection.provisioning.job'].create({'tenant_id': tenant.id, 'database_name': db})

    def _validate(self, job, status, page=200, opened='ready'):
        with patch.object(TenantRelease, '_nc_release_call', return_value=status), \
                patch.object(TenantRelease, '_nc_release_page', return_value=page), \
                patch.object(TenantRelease, '_nc_release_open', return_value=opened) as do_open:
            job._nc_validate_release(job.database_name)
        return do_open

    def test_case_d_a_new_tenant_is_opened_only_after_its_gate_validates_it(self):
        job = self._job()
        do_open = self._validate(job, _status('provisioning', hold='provisioning'))
        do_open.assert_called_once()
        self.assertIn('Release validated', job.log or '')

    def test_case_d_validation_refuses_every_unsafe_answer(self):
        held = _status('provisioning', hold='provisioning')
        for label, kwargs in (
                ('behind the code', {'status': _status('upgrade_required', behind=[('ncollection_core', '1', '2')])}),
                ('not held: it was routable', {'status': _status('ready')}),
                ('unreachable', {'status': (None, {'error': 'refused'})}),
                ('sign-in page fails', {'status': held, 'page': 500}),
                ('cannot be opened', {'status': held, 'opened': 'upgrade_required'})):
            with self.subTest(case=label):
                with self.assertRaises(UserError):
                    self._validate(self._job('freshrel%d' % len(label)), **kwargs)

    def test_case_d_a_failed_validation_rolls_the_new_database_back(self):
        job = self._job('rollrel')
        dropped = []
        exists = [False]

        def fake_init(_self, db, modules):
            exists[0] = True

        with patch.object(type(job), '_database_exists', new=lambda _s, db: exists[0]), \
                patch.object(type(job), '_drop_database', new=lambda _s, db: dropped.append(db)), \
                patch.object(type(job), '_run_odoo_init', new=fake_init), \
                patch.object(type(job), '_module_list', return_value=['base']), \
                patch.object(type(job), '_verify_localization'), \
                patch.object(type(job), '_seed_tenant', return_value=None), \
                patch.object(TenantRelease, '_nc_release_call', return_value=_status('ready')):
            ok = job.run_provisioning()
        self.assertFalse(ok)
        self.assertEqual(job.status, 'failed')
        self.assertEqual(dropped, ['rollrel'], 'the database this run created is rolled back')
        self.assertNotEqual(job.tenant_id.database_status, 'ready', 'the tenant never became ready')

    def test_case_d_the_install_runs_with_the_provisioning_hold(self):
        job = self._job('holdrel')
        seen = {}

        def fake_run(_self, cmd, label, stdin=None, env=None, timeout=None):
            seen['env'] = env
            return ''

        with patch.object(type(job), '_run_odoo_subprocess', new=fake_run):
            job._run_odoo_init('holdrel', ['base'])
        self.assertEqual((seen['env'] or {}).get('NC_RELEASE_HOLD'), 'provisioning')

    # ------------------------------------------------------------------ Fleet Migration: the exact module set
    def _line_both(self, status, modules=('ncollection_core', 'ncollection_branding', 'ncollection_auth'),
                   licensed=(), not_installed=()):
        """``(upgrade list, install list)`` the line derives; the licensed set and the tenant's installed state are
        stubbed (the real read is an odoo shell inside the tenant)."""
        migration = self.env['ncollection.fleet.migration'].create({'name': 'rel', 'module_names': ','.join(modules)})
        line = self.env['ncollection.fleet.migration.line'].create({
            'migration_id': migration.id, 'tenant_id': self.atlas.id})
        Line = type(line)
        with patch.object(TenantRelease, '_nc_release_call', return_value=status), \
                patch.object(type(self.atlas), '_nc_licensed_module_list', lambda _s: list(licensed)), \
                patch.object(Line, '_not_installed', lambda _s, _db, mods: [m for m in mods if m in not_installed]):
            return line._nc_required_modules('atlasrel', list(modules))

    def _line(self, status, modules=('ncollection_core', 'ncollection_branding', 'ncollection_auth')):
        return self._line_both(status, modules)[0]

    def test_an_old_tenant_gains_what_the_code_auto_installs_and_what_it_is_licensed_for(self):
        """Parity with a tenant created today: the gate's ``absent`` list (auto-installed by the code) and the
        licensed modules this tenant lacks are INSTALLED in the same run; nothing else is."""
        upgrade, install = self._line_both(
            _status('upgrade_required', absent=['ncollection_purchase_documents', 'ncollection_sale_documents']),
            licensed=['ncollection_account_core', 'ncollection_cheque_print', 'sale'],
            not_installed=['ncollection_cheque_print', 'sale'])
        self.assertEqual(upgrade, [])
        self.assertEqual(install, ['ncollection_cheque_print', 'ncollection_purchase_documents',
                                   'ncollection_sale_documents'],
                         'only our modules: a licensed Odoo app is the module-install job, not a release')

    def test_a_tenant_at_parity_gains_nothing(self):
        self.assertEqual(self._line_both(_status('ready'), licensed=['ncollection_cheque_print']), ([], []))

    def test_an_unreadable_licensed_state_refuses_rather_than_guessing(self):
        migration = self.env['ncollection.fleet.migration'].create({'name': 'rel', 'module_names': 'ncollection_core'})
        line = self.env['ncollection.fleet.migration.line'].create({
            'migration_id': migration.id, 'tenant_id': self.atlas.id})
        with patch.object(TenantRelease, '_nc_release_call', return_value=_status('ready')), \
                patch.object(type(self.atlas), '_nc_licensed_module_list', lambda _s: ['ncollection_cheque_print']), \
                patch.object(type(line), '_not_installed', lambda *_a: None), \
                self.assertRaises(UserError):
            line._nc_required_modules('atlasrel', ['ncollection_core'])

    def test_the_line_upgrades_exactly_what_the_tenant_needs(self):
        required = self._line(_status('upgrade_required', behind=[
            ('ncollection_auth', '19.0.1.2.0', '19.0.1.3.0'), ('ncollection_core', '19.0.1.18.0', '19.0.1.23.0')]))
        self.assertEqual(required, ['ncollection_auth', 'ncollection_core'], 'branding is not behind: not upgraded')

    def test_a_current_tenant_needs_nothing(self):
        self.assertEqual(self._line(_status('ready')), [])

    def test_a_migration_that_does_not_cover_what_the_tenant_needs_is_refused(self):
        with self.assertRaises(UserError) as caught:
            self._line(_status('upgrade_required', behind=[('ncollection_account_core', '19.0.1.5.0', '19.0.1.7.0'),
                                                           ('ncollection_core', '19.0.1.18.0', '19.0.1.23.0')]))
        self.assertIn('ncollection_account_core', str(caught.exception))

    def test_a_tenant_that_needs_an_operator_is_not_upgraded(self):
        with self.assertRaises(UserError):
            self._line(_status('blocked', missing=['ncollection_uae_einvoicing']))
        with self.assertRaises(UserError):
            self._line((None, {'error': 'unreachable'}))

    # ---- the platform operator bearer is the SAME derivation the platform uses towards tenants ---------------------
    def test_the_gate_and_the_platform_derive_the_operator_bearer_identically(self):
        """release_gate.py (ncollection_core) cannot import ncollection_saas, so it restates the KDF: pinned here."""
        self.assertEqual(gate._KDF_LABEL, config_sync._KDF_LABEL)
        self.assertEqual(gate.MASTER_ENV, config_sync._SYNC_KEY_ENV)
        master = 'release-compat-test-master-0123456789'
        db = self.env.cr.dbname
        with patch.dict(os.environ, {gate.MASTER_ENV: master}):
            bearer = 'Bearer %s' % config_sync.derive_tenant_key(master, db)
            self.assertTrue(gate.platform_bearer_ok(db, gate.PLATFORM, bearer))
            self.assertFalse(gate.platform_bearer_ok(
                db, gate.PLATFORM, 'Bearer %s' % config_sync.derive_tenant_key(master, 'someothertenant')))
