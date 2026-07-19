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
