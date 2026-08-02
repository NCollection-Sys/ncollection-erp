# -*- coding: utf-8 -*-
"""Platform-side config-sync re-key orchestration (#221).

The tenant-side write is tested in ncollection_core (test_config_sync_key). What
is proved here is the orchestration around it: who may run it, what happens when
the master is missing, that one failing tenant cannot abort a fleet rotation, and
that a "successful" subprocess is NOT reported as success until a real push has
authenticated.
"""

from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged
from odoo.tools import mute_logger

from odoo.addons.ncollection_saas.models.config_sync import (
    _SYNC_KEY_ENV, _TENANT_KEY_ENV)

REKEY = 'odoo.addons.ncollection_saas.models.config_sync_rekey.TenantConfigSyncRekey'
MIXIN = 'odoo.addons.ncollection_saas.models.saas_subprocess.SaasSubprocessMixin'
LOGGER = 'odoo.addons.ncollection_saas.models.config_sync_rekey'
# sync_workspace_config is declared on the config_sync class, not on the re-key
# one. mock.patch resolves the attribute on the class it is NAMED with, so
# patching it via REKEY raises AttributeError even though the method is
# reachable on the model — the same lesson #243 hit when a helper moved to a mixin.
SYNC = 'odoo.addons.ncollection_saas.models.config_sync.TenantConfigSync'


@tagged('post_install', '-at_install')
class TestConfigSyncRekey(TransactionCase):

    def setUp(self):
        super().setUp()
        self.plan = self.env['ncollection.subscription.plan'].create({
            'name': 'Rekey Plan', 'code': 'rekey', 'max_users': 5,
        })
        self.tenant = self._tenant('rekeyclienta')
        self.other = self._tenant('rekeyclientb')

    def _tenant(self, db):
        return self.env['ncollection.tenant'].create({
            'company_name': db,
            'database_name': db,
            'database_status': 'ready',
            'plan_id': self.plan.id,
        })

    # ---- access control (Rule 4: mirror the button at the ORM) -----------

    def test_a_non_admin_cannot_rekey(self):
        """The button carries groups=, but a groups= attribute only hides a
        button — the method is still reachable over RPC, and it spawns
        subprocesses against tenant databases."""
        user = self.env['res.users'].create({
            'name': 'Plain User', 'login': 'rekey-plain-user',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])],
        })
        with self.assertRaises(UserError):
            self.tenant.with_user(user).action_rekey_config_sync()

    def test_a_non_admin_cannot_rekey_the_fleet(self):
        user = self.env['res.users'].create({
            'name': 'Plain User 2', 'login': 'rekey-plain-user-2',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])],
        })
        with self.assertRaises(UserError):
            self.env['ncollection.tenant'].with_user(
                user).action_rekey_config_sync_fleet()

    # ---- refusing a misconfiguration ------------------------------------

    def test_a_missing_master_refuses_instead_of_reporting_success(self):
        """The #219 lesson. Without the master no key can be derived; the tempting
        behaviour is to skip every tenant and return a tidy summary, which an
        operator would read as a completed rotation."""
        with patch.dict('os.environ', {}, clear=False) as env_map:
            env_map.pop(_SYNC_KEY_ENV, None)
            with self.assertRaises(UserError):
                self.tenant.action_rekey_config_sync()

    # ---- marker parsing --------------------------------------------------

    def test_the_last_marker_wins(self):
        """odoo shell prints its own banner and warnings around our output, so
        taking the FIRST match would let unrelated noise decide the outcome."""
        model = self.env['ncollection.tenant']
        out = 'REKEY_ERR early noise\nsome shell chatter\nREKEY_OK\n'
        self.assertEqual(model._rekey_marker(out), 'REKEY_OK')

    def test_no_marker_is_an_error_not_a_success(self):
        model = self.env['ncollection.tenant']
        self.assertTrue(model._rekey_marker('').startswith('REKEY_ERR'))
        self.assertTrue(model._rekey_marker('unrelated\n').startswith('REKEY_ERR'))

    def test_a_skip_marker_is_not_reported_as_ok(self):
        model = self.env['ncollection.tenant']
        marker = model._rekey_marker('REKEY_SKIPPED_NO_ACCOUNT\n')
        self.assertEqual(marker, 'REKEY_SKIPPED_NO_ACCOUNT')

    # ---- the master never reaches the tenant subprocess ------------------

    def test_the_master_is_scrubbed_from_the_subprocess_env(self):
        """#212's core contract. The subprocess must receive ONLY the derived
        per-tenant key; a master leaking into a tenant context would make every
        tenant's key forgeable from inside one tenant."""
        captured = {}

        def _fake_run(_self, cmd, label, stdin=None, env=None, **kw):
            captured['env'] = env or {}
            return 'REKEY_OK\n'

        with patch.dict('os.environ', {_SYNC_KEY_ENV: 'the-master-secret'}), \
                patch('%s._run_odoo_subprocess' % MIXIN, _fake_run), \
                patch('%s._rekey_verify' % REKEY,
                      lambda self, db: (self, True, 'ok')):
            self.tenant.action_rekey_config_sync()

        self.assertNotIn(_SYNC_KEY_ENV, captured['env'],
                         "the platform master reached the tenant subprocess")
        self.assertIn(_TENANT_KEY_ENV, captured['env'])
        self.assertNotEqual(captured['env'][_TENANT_KEY_ENV], 'the-master-secret')

    # ---- verification is not optional -----------------------------------

    @mute_logger(LOGGER)
    def test_a_written_key_that_does_not_authenticate_is_reported_failed(self):
        """The false-green this repo keeps getting bitten by, in this ticket's
        shape: the subprocess says REKEY_OK, but the push still 401s. Reporting
        that as success would hand an operator a fleet locked out of its own
        licence enforcement."""
        with patch.dict('os.environ', {_SYNC_KEY_ENV: 'master'}), \
                patch('%s._run_odoo_subprocess' % MIXIN,
                      lambda *a, **kw: 'REKEY_OK\n'), \
                patch('%s.sync_workspace_config' % SYNC, lambda self: True):
            self.tenant.config_sync_state = 'permanent'
            self.tenant.config_sync_last_error = 'HTTP 401'
            result = self.tenant._rekey_one('master')

        self.assertFalse(result[1], "a still-401ing tenant was reported as ok")
        self.assertIn('401', result[2])

    @mute_logger(LOGGER)
    def test_one_failing_tenant_does_not_abort_the_fleet(self):
        """A rotation that dies half-way leaves an operator unable to tell which
        tenants carry which key — strictly worse than a partial run that says so."""
        def _run(_self, cmd, label, stdin=None, env=None, **kw):
            if 'rekeyclienta' in ' '.join(cmd):
                raise RuntimeError('tenant unreachable')
            return 'REKEY_OK\n'

        with patch.dict('os.environ', {_SYNC_KEY_ENV: 'master'}), \
                patch('%s._run_odoo_subprocess' % MIXIN, _run), \
                patch('%s._rekey_verify' % REKEY,
                      lambda self, db: (self, True, 'ok')):
            both = self.tenant | self.other
            action = both.action_rekey_config_sync()

        # The good tenant still got done, and the summary is loud about the bad one.
        self.assertEqual(action['params']['type'], 'warning')
        self.assertTrue(action['params']['sticky'])
        self.assertIn('rekeyclienta', action['params']['message'])

    @mute_logger(LOGGER)
    def test_a_tenant_without_a_ready_database_is_skipped_not_attempted(self):
        self.tenant.database_status = 'not_provisioned'
        with patch.dict('os.environ', {_SYNC_KEY_ENV: 'master'}), \
                patch('%s._run_odoo_subprocess' % MIXIN,
                      lambda *a, **kw: self.fail('subprocess ran for a tenant '
                                                 'with no database')):
            result = self.tenant._rekey_one('master')
        self.assertFalse(result[1])
        self.assertIn('no ready database', result[2])

    # ---- audit trail -----------------------------------------------------

    def test_every_rekey_lands_in_the_tenant_chatter(self):
        """A key rotation is a security event and has to be attributable. It rides
        ncollection.tenant's existing mail.thread (#264) rather than earning a
        model and a table of its own."""
        before = len(self.tenant.message_ids)
        with patch.dict('os.environ', {_SYNC_KEY_ENV: 'master'}), \
                patch('%s._run_odoo_subprocess' % MIXIN,
                      lambda *a, **kw: 'REKEY_OK\n'), \
                patch('%s._rekey_verify' % REKEY,
                      lambda self, db: (self, True, 'ok')):
            self.tenant.action_rekey_config_sync()
        self.assertGreater(len(self.tenant.message_ids), before)
        self.assertIn('re-keyed', self.tenant.message_ids[0].body.lower())
