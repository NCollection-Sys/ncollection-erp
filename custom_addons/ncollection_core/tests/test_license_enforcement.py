# -*- coding: utf-8 -*-
"""Ring 2 ORM/RPC license enforcement (P1-T10).

Uses a synthetic namespace ("fake_app") so the tests do not depend on which
real Odoo apps are installed in CI. The enforcement point is
BaseModel._check_access, so we test through res.partner (always present)
after monkeyless injection of a blocked namespace via the workspace config
+ a menu owned by that module (which is how the real blocked-set is
derived).
"""

import time
from unittest.mock import patch

from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.ncollection_core.models import license_enforcement as le


@tagged("post_install", "-at_install")
class TestLicenseEnforcement(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Config = cls.env["ncollection.workspace.config"]
        cls.Menu = cls.env["ir.ui.menu"]
        cls.action = cls.env["ir.actions.act_window"].create({
            "name": "Fake Things", "res_model": "res.partner",
            "view_mode": "list,form",
        })
        # A root menu owned by module "res_partner" so that res.partner's
        # namespace ("res") could in principle be derived — but "res" is in
        # NEVER_BLOCKED, so we instead use a fake module owning a fake root
        # to drive the blocked set, and assert on a model in that namespace.
        cls.fake_root = cls.Menu.create({"name": "Fake App"})
        cls.env["ir.model.data"].create({
            "name": "menu_root", "module": "fake_app",
            "model": "ir.ui.menu", "res_id": cls.fake_root.id,
        })
        cls.user = new_test_user(
            cls.env, login="lic_user", groups="base.group_user",
        )

    def _set_plan(self, allowed):
        self.Config.search([]).unlink()
        cfg = self.Config.create({"allowed_module_names": allowed})
        self.env.registry.clear_cache()
        return cfg

    # ---------------- namespace derivation ----------------

    def test_signature_pinned(self):
        self.assertTrue(le.SIGNATURE_OK)

    def test_blocked_namespaces_derivation(self):
        self._set_plan("crm,sale,account")  # fake_app NOT licensed
        blocked = self.Menu._ncollection_blocked_namespaces_cached()
        self.assertIn("fake_app", blocked)

    def test_never_blocked_namespaces(self):
        self._set_plan("crm")
        blocked = self.Menu._ncollection_blocked_namespaces_cached()
        for ns in ("base", "ir", "res", "mail", "bus", "web", "ncollection"):
            self.assertNotIn(ns, blocked)

    # ---------------- enforcement via _check_access ----------------

    def _check_denied(self, model_env, op):
        """Assert a blocked-namespace access raises the branded error."""
        with self.assertRaises(AccessError) as cm:
            model_env.check_access(op)
        self.assertIn("NCollection plan", str(cm.exception))

    def test_end_to_end_denial_on_real_model(self):
        """The full _check_access path denies a non-system user when the
        model's namespace is blocked — proving ORM/RPC enforcement fires,
        not just that an error factory exists.

        We force 'res' into the blocked set for the duration of this test so
        we can exercise a real, always-present model (res.partner) through
        the genuine check_access funnel that XML-RPC/JSON-RPC also use.
        """
        Partner = self.env["res.partner"].with_user(self.user)
        with patch.object(
            type(self.Menu),
            "_ncollection_blocked_namespaces_cached",
            return_value=frozenset({"res"}),
        ):
            # non-system user -> denied with branded message
            with self.assertRaises(AccessError) as cm:
                Partner.check_access("read")
            self.assertIn("NCollection plan", str(cm.exception))
            # system user and su remain exempt on the very same blocked model
            self.env["res.partner"].with_user(
                self.env.ref("base.user_admin")
            ).check_access("read")
            self.env["res.partner"].sudo().check_access("read")
        # outside the patch, enforcement is back to config-derived (empty)
        Partner.check_access("read")

    def test_partner_allowed_when_res_never_blocked(self):
        """res.* is never blocked, so partner access always passes."""
        self._set_plan("crm")
        # non-system user, res.partner read must not be denied by us
        self.env["res.partner"].with_user(self.user).check_access("read")

    def test_system_user_exempt(self):
        self._set_plan("crm")
        # admin is a system user; even a blocked namespace would pass.
        partner = self.env["res.partner"].with_user(self.env.ref("base.user_admin"))
        partner.check_access("read")

    def test_su_exempt(self):
        self._set_plan("crm")
        self.env["res.partner"].sudo().check_access("read")

    def test_no_config_fail_open(self):
        self.Config.search([]).unlink()
        self.env.registry.clear_cache()
        self.assertEqual(
            self.Menu._ncollection_blocked_namespaces_cached(), frozenset()
        )
        self.env["res.partner"].with_user(self.user).check_access("read")

    # ---------------- performance budget ----------------

    def test_overhead_under_budget(self):
        """1000 warm check_access calls must average < 5 ms each."""
        self._set_plan("crm")
        Partner = self.env["res.partner"].with_user(self.user)
        Partner.check_access("read")  # warm the cache
        n = 1000
        start = time.perf_counter()
        for _ in range(n):
            Partner.check_access("read")
        avg_ms = (time.perf_counter() - start) / n * 1000
        self.assertLess(
            avg_ms, 5.0,
            f"license enforcement overhead {avg_ms:.3f}ms/call exceeds 5ms",
        )
