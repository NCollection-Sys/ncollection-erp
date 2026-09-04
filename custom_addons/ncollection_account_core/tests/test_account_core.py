# -*- coding: utf-8 -*-
"""ncollection_account_core (F1-T01) — shared mixin unit tests.

Prove the subscription-restriction helpers integrate with the platform's
existing license source of truth (#11, ncollection.workspace.config), mirror
Ring-2's system-user exemption + fail-open, and fail loud on bad input. No
accounting-engine behaviour is exercised — Odoo owns that; this module only
adds glue.

The gate exempts system/sudo callers (like Ring-2), so the DENY-path tests run
as a NON-system internal user; the exemption itself is asserted separately.
"""
from unittest.mock import patch

from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestAccountCoreMixin(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Config = cls.env['ncollection.workspace.config']
        # A plain internal user (base.group_user only) is NOT a system user, so
        # the license gate actually applies to it — unlike the admin running the
        # test, whom Ring-2 (and therefore this mixin) exempts.
        cls.user = cls.env['res.users'].create({
            'name': 'Fin User',
            'login': 'nc_account_core_finuser',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])],
        })
        # The mixin as seen by that non-system user (the realistic gated caller).
        cls.Gated = cls.env['ncollection.account.mixin'].with_user(cls.user)
        # The mixin as the (system) admin — used to prove the exemption.
        cls.AsAdmin = cls.env['ncollection.account.mixin']

    def _set_allowed(self, names):
        """Force the singleton workspace config to a given allowed-module list,
        the way the platform config-sync (#11) would land the plan's list.
        Create-or-write, since the model enforces a per-tenant singleton."""
        config = self.Config.sudo().get_config()
        if config:
            config.write({'allowed_module_names': names})
        else:
            config = self.Config.sudo().create({'allowed_module_names': names})
        return config

    # ---- install ---------------------------------------------------------

    def test_module_installed(self):
        module = self.env['ir.module.module'].search(
            [('name', '=', 'ncollection_account_core')], limit=1)
        self.assertEqual(module.state, 'installed',
                         "ncollection_account_core must install cleanly on a tenant DB")

    # ---- entitlement gate reads the #11 source of truth (non-system user) --

    def test_feature_enabled_when_licensed(self):
        self._set_allowed('account,sale')
        self.assertTrue(self.Gated._nc_feature_enabled('account'))

    def test_feature_disabled_when_not_licensed(self):
        self._set_allowed('sale')
        self.assertFalse(self.Gated._nc_feature_enabled('account'))

    def test_gate_matches_workspace_config_list_exactly(self):
        """The gate must reflect ncollection.workspace.config.get_allowed_module_list()
        — the exact list Ring-2 ORM enforcement keys on — never a parallel list."""
        config = self._set_allowed('crm , account ,crm')  # whitespace + dupe
        self.assertEqual(config.get_allowed_module_list(), ['crm', 'account'])
        for name in ('crm', 'account'):
            self.assertTrue(self.Gated._nc_feature_enabled(name))
        self.assertFalse(self.Gated._nc_feature_enabled('stock'))

    # ---- Ring-2 parity: system/sudo exemption ----------------------------

    def test_system_user_is_exempt(self):
        """Like Ring-2, a system user is never license-gated — even for a
        feature the plan omits."""
        self._set_allowed('sale')  # 'account' NOT licensed
        self.assertTrue(self.AsAdmin._nc_feature_enabled('account'),
                        "system user must be exempt from the feature gate")

    def test_sudo_is_exempt(self):
        self._set_allowed('sale')
        self.assertTrue(self.Gated.sudo()._nc_feature_enabled('account'),
                        "a sudo() call must be exempt (env.su), matching Ring-2")

    # ---- fail-open contract (house style) --------------------------------

    def test_fail_open_when_allowed_list_empty(self):
        self._set_allowed('')  # empty list == "no filtering"
        self.assertTrue(self.Gated._nc_feature_enabled('anything'))

    def test_fail_open_when_no_config(self):
        with patch.object(type(self.Config), 'get_config',
                          return_value=self.Config.browse()):
            self.assertTrue(self.Gated._nc_feature_enabled('account'))

    # ---- input validation: fail loud, not silently "not licensed" --------

    def test_blank_module_name_raises(self):
        for bad in (None, '', '   '):
            with self.assertRaises(ValueError):
                self.Gated._nc_feature_enabled(bad)

    # ---- imperative helper -----------------------------------------------

    def test_require_feature_raises_when_not_licensed(self):
        self._set_allowed('sale')
        with self.assertRaises(AccessError):
            self.Gated._nc_require_feature('account')

    def test_require_feature_passes_when_licensed(self):
        self._set_allowed('account')
        self.Gated._nc_require_feature('account')  # must NOT raise


@tagged('post_install', '-at_install')
class TestAccountingGroupHierarchy(TransactionCase):
    """readonly < user < manager, so the native reports are reachable (#474).

    Odoo 19 declares the three accounting groups independently. Every ACL in
    ncollection_account_reports is keyed on `group_account_readonly`, so
    without this containment an "Accounting / Administrator" gets AccessError
    on Trial Balance, General Ledger, Balance Sheet, the VAT 201 — and Odoo's
    menu filter then hides every one of those menus. Measured on a live tenant
    before the fix: manager=True, user=False, readonly=False.
    """

    def _user(self, xmlid):
        return self.env['res.users'].create({
            'name': 'Acc Role', 'login': 'accrole-%s@example.test' % xmlid.split('.')[-1],
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id,
                                  self.env.ref(xmlid).id])]})

    def test_an_accounting_manager_holds_the_readonly_feature_group(self):
        user = self._user('account.group_account_manager')
        self.assertTrue(user.has_group('account.group_account_user'))
        self.assertTrue(user.has_group('account.group_account_readonly'))

    def test_an_accountant_holds_the_readonly_feature_group(self):
        self.assertTrue(
            self._user('account.group_account_user').has_group(
                'account.group_account_readonly'))

    def test_a_manager_can_actually_read_a_native_report_model(self):
        """The reachability this exists for. Skipped only where the report
        engine is absent, so it cannot pass vacuously where it is present."""
        if 'ncollection.account.report.trial.balance' not in self.env:
            self.skipTest('ncollection_account_reports is not installed here')
        user = self._user('account.group_account_manager')
        self.env['ncollection.account.report.trial.balance'].with_user(
            user).check_access('read')

    def test_a_plain_employee_still_cannot(self):
        """The control: the fix must not have opened the reports to everyone."""
        user = self.env['res.users'].create({
            'name': 'Plain', 'login': 'plain-acc@example.test',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        self.assertFalse(user.has_group('account.group_account_readonly'))
