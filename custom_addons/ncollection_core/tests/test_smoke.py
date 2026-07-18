# -*- coding: utf-8 -*-
"""Smoke test — proves the module installs and the CI test runner executes.

Every future test module in this addon gets imported from tests/__init__.py
next to this one. Keep this file green: it is the canary that the addon's
import chain (__init__.py -> controllers/models/wizards) is intact.
"""

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestNcollectionCoreSmoke(TransactionCase):

    def test_module_installed(self):
        """The module record exists and is in the 'installed' state."""
        module = self.env["ir.module.module"].search(
            [("name", "=", "ncollection_core")]
        )
        self.assertTrue(module, "ncollection_core must be known to Odoo")
        self.assertEqual(
            module.state, "installed",
            "ncollection_core must be installed when its tests run",
        )

    def test_env_sanity(self):
        """The ORM environment is functional (registry loaded, cursor live)."""
        self.assertTrue(self.env["res.users"].browse(self.env.uid).exists())
