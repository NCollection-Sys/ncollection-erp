# -*- coding: utf-8 -*-
"""Smoke test — proves the module installs and the CI test runner executes.

Also asserts the dependency chain: installing ncollection_saas must pull in
ncollection_subscription (its manifest dependency).
"""

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestNcollectionSaasSmoke(TransactionCase):

    def test_module_installed(self):
        """The module record exists and is in the 'installed' state."""
        module = self.env["ir.module.module"].search(
            [("name", "=", "ncollection_saas")]
        )
        self.assertTrue(module, "ncollection_saas must be known to Odoo")
        self.assertEqual(
            module.state, "installed",
            "ncollection_saas must be installed when its tests run",
        )

    def test_dependency_installed(self):
        """The declared dependency (ncollection_subscription) came along."""
        dep = self.env["ir.module.module"].search(
            [("name", "=", "ncollection_subscription")]
        )
        self.assertEqual(
            dep.state, "installed",
            "ncollection_subscription must be installed as a dependency",
        )
