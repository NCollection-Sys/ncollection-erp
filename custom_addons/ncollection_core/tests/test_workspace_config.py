# -*- coding: utf-8 -*-
"""Workspace config: singleton, parsing, access rights (P1-T09)."""

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestWorkspaceConfig(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Config = cls.env["ncollection.workspace.config"]

    def test_singleton_enforced(self):
        self.Config.create({"plan_code": "STARTER"})
        with self.assertRaises(ValidationError):
            self.Config.create({"plan_code": "SECOND"})

    def test_get_config_empty_then_filled(self):
        self.assertFalse(self.Config.get_config())
        cfg = self.Config.create({"plan_code": "STARTER"})
        self.assertEqual(self.Config.get_config(), cfg)

    def test_allowed_module_list_parsing(self):
        cfg = self.Config.create({
            "allowed_module_names": " crm, sale ,account,,crm ",
        })
        self.assertEqual(cfg.get_allowed_module_list(), ["crm", "sale", "account"])

    def test_allowed_module_list_empty(self):
        cfg = self.Config.create({"plan_code": "X"})
        self.assertEqual(cfg.get_allowed_module_list(), [])

    def test_internal_user_reads_but_cannot_write(self):
        cfg = self.Config.create({"plan_code": "STARTER"})
        user = new_test_user(self.env, login="wc_user", groups="base.group_user")
        # read allowed (menus are computed in user context)
        self.assertEqual(cfg.with_user(user).plan_code, "STARTER")
        # write/create/unlink denied (platform-only surface)
        with self.assertRaises(AccessError):
            cfg.with_user(user).write({"plan_code": "HACKED"})
        with self.assertRaises(AccessError):
            self.Config.with_user(user).create({"plan_code": "X"})
        with self.assertRaises(AccessError):
            cfg.with_user(user).unlink()


@tagged("post_install", "-at_install")
class TestRequiredCronSelfReport(TransactionCase):
    """The tenant's half of the fleet cron check (#262).

    The tenant reports on ITSELF over the existing config-sync response, which
    is what keeps Rule 3 intact: the platform never queries a tenant model.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Cfg = cls.env['ncollection.workspace.config']

    def test_report_shape_is_one_entry_per_required_cron(self):
        # Relative import: pylint-odoo W8150 flags an absolute
        # odoo.addons.<own module> import from inside the same module.
        from ..models.workspace_config import REQUIRED_CRON_XMLIDS
        report = self.Cfg._required_cron_health()
        self.assertEqual(set(report), set(REQUIRED_CRON_XMLIDS))
        for info in report.values():
            self.assertIn('installed', info)
            self.assertIn('active', info)

    def test_an_unresolvable_cron_reports_not_installed_rather_than_raising(self):
        """An xml-id that resolves to nothing must read as 'not installed'.

        ncollection_core must not depend on ncollection_auth, so on a tenant
        without it env.ref() finds nothing — that is a legitimate state (#218
        backfills those), not an error and not a disabled job.

        Uses a SYNTHETIC xml-id rather than checking whether ncollection_auth
        happens to be installed. The earlier version guarded its assertions
        behind `if 'ncollection_auth' not in ..._installed()`, and CI installs
        ncollection_auth alongside ncollection_core — so in the one place it
        mattered the condition was always False and the test asserted nothing
        while reporting green.
        """
        report = self.Cfg.with_context(
            _nc_required_crons=('ncollection_nonexistent.cron_nope',)
        )._required_cron_health()
        entry = report['ncollection_nonexistent.cron_nope']
        self.assertFalse(entry['installed'],
                         "an unresolvable xml-id is 'not installed'")
        self.assertFalse(entry['active'])
        self.assertNotIn('error', entry,
                         "a missing module is not an error condition")

    def test_report_reflects_the_live_active_flag(self):
        cron = self.env.ref('ncollection_auth.cron_gc_auth_log',
                            raise_if_not_found=False)
        if not cron:
            self.skipTest('ncollection_auth is not installed on this database')
        cron.active = False
        report = self.Cfg._required_cron_health()
        self.assertTrue(report['ncollection_auth.cron_gc_auth_log']['installed'])
        self.assertFalse(
            report['ncollection_auth.cron_gc_auth_log']['active'],
            "the report must read the live flag, not a cached assumption")
