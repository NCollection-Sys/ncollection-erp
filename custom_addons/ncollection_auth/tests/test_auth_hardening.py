# -*- coding: utf-8 -*-
"""P1-T19 acceptance tests: auth events are logged, the core login-cooldown
lockout honors our hardened parameters, and the log is locked down at the ORM.
"""

from odoo.exceptions import AccessDenied, AccessError
from odoo.fields import Datetime
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestAuthHardening(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.AuthLog = cls.env['ncollection.auth.log']
        cls.user = cls.env['res.users'].create({
            'name': 'Auth Test User',
            'login': 'auth_test_user',
            # _action_reset_password refuses users without an email address.
            'email': 'auth_test_user@example.com',
            'password': 'CorrectHorse!42',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])],
        })
        # _check_credentials verifies the stored hash at SQL level — the
        # freshly created user must be flushed or the check reads nothing.
        cls.env.flush_all()

    # ---- audit log events ------------------------------------------------

    def test_login_success_logged(self):
        before = self.AuthLog.search_count([('event_type', '=', 'login_success')])
        self.user._check_credentials(
            {'type': 'password', 'password': 'CorrectHorse!42'}, {'interactive': True},
        )
        after = self.AuthLog.search_count([('event_type', '=', 'login_success')])
        self.assertEqual(after, before + 1, 'a login_success row must be logged')
        row = self.AuthLog.search(
            [('event_type', '=', 'login_success')], order='id desc', limit=1)
        self.assertEqual(row.login, 'auth_test_user')
        self.assertEqual(row.user_id, self.user)
        self.assertEqual(row.db_name, self.env.cr.dbname)

    def test_login_failure_logged_isolated(self):
        """Failure rows are written via a separate cursor so they survive the
        rollback; they therefore persist OUTSIDE this test's transaction and
        must be cleaned up the same way."""
        with self.assertRaises(AccessDenied):
            self.user._check_credentials(
                {'type': 'password', 'password': 'wrong-password'},
                {'interactive': True},
            )
        rows = self.AuthLog.search([
            ('event_type', '=', 'login_failed'), ('login', '=', 'auth_test_user'),
        ])
        self.assertTrue(rows, 'a login_failed row must survive the rollback')
        # committed by the isolated cursor -> remove it the same way
        self.AuthLog._capture_cleanup_for_tests('auth_test_user')

    def test_reset_request_logged(self):
        before = self.AuthLog.search_count([('event_type', '=', 'reset_request')])
        self.user._action_reset_password(signup_type='reset')
        after = self.AuthLog.search_count([('event_type', '=', 'reset_request')])
        self.assertEqual(after, before + 1)

    # ---- lockout (core login cooldown driven by our parameters) ----------

    def test_cooldown_locks_after_configured_failures(self):
        """Our data params arm core's _on_login_cooldown: above the failure
        threshold and inside the window, login is refused."""
        icp = self.env['ir.config_parameter'].sudo()
        self.assertEqual(icp.get_param('base.login_cooldown_after'), '5')
        self.assertEqual(icp.get_param('base.login_cooldown_duration'), '300')
        now = Datetime.now()
        self.assertTrue(
            self.user._on_login_cooldown(6, now),
            '6 recent failures with threshold 5 must be on cooldown',
        )
        self.assertFalse(
            self.user._on_login_cooldown(4, now),
            '4 failures stays under the threshold',
        )

    def test_cooldown_feature_flag_disable(self):
        """Setting base.login_cooldown_after = 0 disables the mechanism —
        the documented feature flag of the minimal-equivalent decision."""
        self.env['ir.config_parameter'].sudo().set_param('base.login_cooldown_after', '0')
        self.assertFalse(self.user._on_login_cooldown(100, Datetime.now()))

    # ---- session timeout (OCA auth_session_timeout) ----------------------

    def test_session_timeout_param_installed(self):
        self.assertEqual(
            self.env['ir.config_parameter'].sudo().get_param(
                'inactive_session_time_out_delay'),
            '7200',
        )

    def test_session_timeout_module_installed(self):
        module = self.env['ir.module.module'].search(
            [('name', '=', 'auth_session_timeout')])
        self.assertEqual(module.state, 'installed')

    # ---- ORM lockdown (Rule 4: restriction mirrored at the ORM) ----------

    def test_log_denied_to_internal_user(self):
        log_as_user = self.AuthLog.with_user(self.user)
        with self.assertRaises(AccessError):
            log_as_user.search([])
        with self.assertRaises(AccessError):
            log_as_user.create({'event_type': 'logout'})
