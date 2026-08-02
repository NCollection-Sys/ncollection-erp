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

    def test_absent_module_reports_not_installed_rather_than_raising(self):
        """ncollection_core must not depend on ncollection_auth.

        On a database without it, env.ref() finds nothing — that has to read as
        'not installed', not as an error and not as a disabled job.
        """
        report = self.Cfg._required_cron_health()
        entry = report['ncollection_auth.cron_gc_auth_log']
        if 'ncollection_auth' not in self.env['ir.module.module']._installed():
            self.assertFalse(entry['installed'])
            self.assertFalse(entry['active'])

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
