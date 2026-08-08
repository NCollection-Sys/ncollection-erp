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

from odoo.addons.ncollection_core.models import ir_ui_menu as iuim
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
        """Still true for a real interactive system user. Scope made explicit
        by the cron tests below (#347)."""
        self._set_plan("crm")
        # admin is a system user; even a blocked namespace would pass.
        partner = self.env["res.partner"].with_user(self.env.ref("base.user_admin"))
        partner.check_access("read")

    def test_su_exempt(self):
        self._set_plan("crm")
        self.env["res.partner"].sudo().check_access("read")

    # ---------------------------------------------------------- #347: crons
    #
    # ir.cron without an explicit user_id runs as SUPERUSER, and su bypasses
    # the ENTIRE access-check machinery: check_access(), has_access() and
    # _filtered_access() all short-circuit on env.su BEFORE this mixin's
    # _check_access override is reached, and search() ignores ACLs outright.
    # So the hole could not be closed by patching the licence hook — the bypass
    # lives above it, in core, by design. The fix is that crons now run as a
    # real, non-superuser identity.

    def test_the_cron_service_user_exists_and_is_not_a_system_user(self):
        """The whole fix rests on this account NOT being system.

        LicenseEnforcementMixin exempts `_is_system()` as well as su, so a
        scheduler user carrying base.group_system would reintroduce #347
        through the other half of the same condition.
        """
        user = self.env.ref("ncollection_core.user_cron_service")
        self.assertTrue(user.active)
        self.assertFalse(user._is_system(), "the scheduler user must not be a "
                                            "system user (#347)")
        self.assertTrue(user.has_group("base.group_user"))

    def test_the_anomaly_crons_run_as_that_user_not_as_superuser(self):
        """A cron with no user_id runs as uid 1 — which is the bug. Binding is
        what makes every other test here meaningful."""
        expected = self.env.ref("ncollection_core.user_cron_service")
        for xmlid in ("ncollection_core.cron_detect_anomalies",
                      "ncollection_core.cron_send_alert_digest"):
            cron = self.env.ref(xmlid)
            self.assertEqual(cron.user_id, expected, xmlid)
            self.assertNotEqual(cron.user_id.id, 1, "%s runs as SUPERUSER, so "
                                                    "Ring 2 cannot gate it" % xmlid)

    def test_a_blocked_model_is_denied_to_the_cron_user(self):
        """CRITERION 2. Before #347 the equivalent path could not deny anything,
        because the job ran as superuser."""
        cron_user = self.env.ref("ncollection_core.user_cron_service")
        with patch.object(
            type(self.Menu),
            "_ncollection_blocked_namespaces_cached",
            return_value=frozenset({"res"}),
        ):
            with self.assertRaises(AccessError) as caught:
                self.env["res.partner"].with_user(cron_user).check_access("read")
            self.assertIn("NCollection plan", str(caught.exception))

    def test_the_aggregation_engine_reports_it_unreadable_for_the_cron_user(self):
        """The consequence that matters: 'a tenant whose plan omits a module
        gets no cron-produced output derived from it'.

        The engine decides what may be aggregated by probing with a read and
        catching AccessError. Under superuser that probe could never raise, so
        every model looked readable — this asserts the real end-state, not just
        that the mixin raises.
        """
        cron_user = self.env.ref("ncollection_core.user_cron_service")
        engine = self.env["ncollection.aggregation.engine"]
        with patch.object(
            type(self.Menu),
            "_ncollection_blocked_namespaces_cached",
            return_value=frozenset({"res"}),
        ):
            self.assertFalse(
                engine.with_user(cron_user)._model_readable("res.partner"))
            # And the old behaviour, which is why this was invisible:
            self.assertTrue(engine.sudo()._model_readable("res.partner"))

    def test_the_cron_user_still_reaches_what_the_plan_DOES_include(self):
        """The other half. A fix that denied crons everything would pass every
        test above and break scheduled work on every tenant."""
        cron_user = self.env.ref("ncollection_core.user_cron_service")
        with patch.object(
            type(self.Menu),
            "_ncollection_blocked_namespaces_cached",
            return_value=frozenset({"mis"}),
        ):
            self.env["res.partner"].with_user(cron_user).check_access("read")

    # ------------------------------------------- the fail-closed guard itself
    #
    # A reviewer replaced this guard's entire body with `return True` and all
    # 204 tests still passed. The guard IS the safety net #347 is about — the
    # thing that stops a cron silently reverting to superuser — and nothing
    # asserted on it. These do.

    def _alert_model(self):
        return self.env["ncollection.alert"]

    def test_the_guard_refuses_to_run_as_superuser(self):
        """The exact condition #347 exists to prevent. su bypasses the whole
        access-check machinery, so running is worse than not running."""
        self.assertFalse(self._alert_model().sudo()._cron_identity_ok())

    def test_the_guard_refuses_to_run_as_a_system_user(self):
        """_is_system() is exempt from licence enforcement too, so a system
        identity is just as inert as su."""
        admin = self.env.ref("base.user_admin")
        self.assertFalse(
            self._alert_model().with_user(admin)._cron_identity_ok())

    def test_the_guard_refuses_when_the_service_user_is_archived(self):
        """An archived user makes ir.cron fall back to superuser silently. That
        fallback is not a degraded mode, it is the bug restored."""
        cron_user = self.env.ref("ncollection_core.user_cron_service")
        model = self._alert_model().with_user(cron_user)
        self.assertTrue(model._cron_identity_ok())     # nominal first
        cron_user.sudo().active = False
        self.addCleanup(lambda: cron_user.sudo().write({"active": True}))
        self.assertFalse(model._cron_identity_ok())

    def test_the_guard_refuses_when_the_xmlid_is_missing(self):
        """Defensive branch: a tenant whose data record was deleted. env.ref is
        called with raise_if_not_found=False, so this must return False rather
        than explode inside a cron."""
        cron_user = self.env.ref("ncollection_core.user_cron_service")
        model = self._alert_model().with_user(cron_user)
        with patch.object(
            type(model), "_CRON_USER_XMLID", "ncollection_core.does_not_exist"
        ):
            self.assertFalse(model._cron_identity_ok())

    def test_the_guard_allows_the_real_scheduler(self):
        """And it must actually permit the nominal case, or every cron on every
        tenant is dead and all four tests above pass anyway."""
        cron_user = self.env.ref("ncollection_core.user_cron_service")
        self.assertTrue(
            self._alert_model().with_user(cron_user)._cron_identity_ok())

    def test_the_scheduler_can_actually_record_an_alert(self):
        """CRITICAL from review: the scheduler is a plain internal user, and
        only base.group_system had create on ncollection.alert. So the fix
        closed the licence bypass and silently discarded every finding — on
        every plan, with correct and broken both looking like 'no alerts'.

        group_cron_service grants create/write on this ONE model and nothing
        else. Asserting the record exists is the point; asserting the cron
        'returned True' is what let this ship.
        """
        cron_user = self.env.ref("ncollection_core.user_cron_service")
        Alert = self._alert_model().with_user(cron_user)
        alert = Alert.create({
            "name": "acl probe",
            "detector_key": "expense_spike",
            "severity": "warning",
            "dedup_key": "acl-probe-347",
        })
        self.assertTrue(alert.id)
        self.assertTrue(alert.exists())

    def test_the_scheduler_account_cannot_authenticate(self):
        """The protection that makes the unrestricted alert read acceptable.

        An earlier version of this file shipped `<field name="password">False
        </field>`, which has no eval= — so Odoo stored the literal STRING
        "False", hashed it, and every tenant got a working login of
        cron@ncollection.internal / False. A reviewer authenticated with it.

        The field is gone and the column is NULL, which makes every verify()
        fail. This asserts that rather than trusting the absence of a line.
        """
        cron_user = self.env.ref("ncollection_core.user_cron_service")
        self.assertFalse(
            cron_user.sudo().password,
            "the scheduler must have no password — a known credential on an "
            "active internal account is a fleet-wide backdoor (#347)")
        self.assertFalse(
            self.env["res.users.apikeys"].sudo().search_count(
                [("user_id", "=", cron_user.id)]),
            "the scheduler must have no API key either")

    def test_the_scheduler_cannot_write_business_models(self):
        """The least-privilege boundary, asserted rather than described.

        The first attempt granted app-user groups under a comment saying "READ
        grants only"; review proved those carry write/create on sale.order and
        UNLINK on stock lots and hr.employee. Only read-only rows remain, and
        this fails if anyone re-broadens them.
        """
        # Relative: pylint-odoo's odoo-addons-relative-import rejects an
        # absolute self-import. tests/ -> `..` is the addon root.
        from ..hooks import SCHEDULER_READ_MODELS

        cron_user = self.env.ref("ncollection_core.user_cron_service")
        checked = []
        for model_name in SCHEDULER_READ_MODELS:
            if model_name not in self.env:
                continue          # module absent — accounted for below
            checked.append(model_name)
            model = self.env[model_name].with_user(cron_user)
            self.assertTrue(model.has_access("read"), model_name)
            for op in ("write", "create", "unlink"):
                self.assertFalse(model.has_access(op),
                                 "%s must not be %s-able by the scheduler"
                                 % (model_name, op))

        # `continue` alone made this pass with ZERO assertions under
        # `make test m=ncollection_core`, which installs none of these modules —
        # a false green on the documented developer workflow. skipTest makes
        # that visible in the log instead of silent.
        if not checked:
            self.skipTest("none of %s installed in this run"
                          % (SCHEDULER_READ_MODELS,))

    def test_no_config_fail_open(self):
        self.Config.search([]).unlink()
        self.env.registry.clear_cache()
        self.assertEqual(
            self.Menu._ncollection_blocked_namespaces_cached(), frozenset()
        )
        self.env["res.partner"].with_user(self.user).check_access("read")

    # ---------------- #240: menu-nested modules ----------------

    def test_nested_modules_are_blockable_candidates(self):
        """#240: account_financial_report / mis_builder nest their menus under
        the account app (no root menu of their own), so root-menu-owner
        derivation never gated them. They are explicit candidates now — blocked
        when the plan omits them, kept when the plan licenses them. (Works
        without the modules installed: they're a static gated set.)"""
        self._set_plan("crm,sale,account")  # neither licensed (a downgrade)
        blocked = self.Menu.sudo()._ncollection_blocked_module_names()
        self.assertIn("account_financial_report", blocked)
        self.assertIn("mis_builder", blocked)
        self._set_plan("account,mis_builder,account_financial_report")  # licensed
        blocked = self.Menu.sudo()._ncollection_blocked_module_names()
        self.assertNotIn("account_financial_report", blocked)
        self.assertNotIn("mis_builder", blocked)

    def test_downgrade_gates_mis_builder_by_actual_namespace(self):
        """#240: a blocked menu-nested module must map to its ACTUAL model
        namespace (mis_builder -> `mis`, Ring 2) — NOT the module name — and its
        nested menus must hide (Ring 1). Runs for real wherever mis_builder is
        installed (CI installs it via ncollection_mis_templates)."""
        if "mis.report" not in self.env:
            self.skipTest("mis_builder not installed in this run")
        self._set_plan("account")  # downgrade: mis_builder not licensed
        # Ring 2: 'mis' is derived from the mis.report model, not the module name
        self.assertIn(
            "mis", self.Menu.sudo()._ncollection_blocked_namespaces_cached())
        # Ring 1: mis_builder's (nested) menus are hidden
        hidden = self.Menu.sudo()._ncollection_blocked_menu_ids()
        menu_md = self.env["ir.model.data"].sudo().search(
            [("model", "=", "ir.ui.menu"), ("module", "=", "mis_builder")], limit=1)
        self.assertTrue(menu_md, "mis_builder owns at least one menu")
        self.assertIn(menu_md.res_id, hidden)
        # re-licensing clears the gate (no permanent block)
        self._set_plan("account,mis_builder")
        self.assertNotIn(
            "mis", self.Menu.sudo()._ncollection_blocked_namespaces_cached())

    def test_shared_infra_namespace_is_not_overblocked(self):
        """#240 over-block guard: a menu-nested module ALSO defines models in
        SHARED namespaces (mis_builder/account_financial_report both define
        `report.*` PDF models). Blocking `report` would deny every module's PDF
        rendering (fail-closed). Only namespaces EXCLUSIVELY owned by blocked
        modules are gated."""
        if "mis.report" not in self.env:
            self.skipTest("mis_builder not installed in this run")
        self._set_plan("account")  # mis_builder blocked
        blocked = self.Menu.sudo()._ncollection_blocked_namespaces_cached()
        self.assertIn("mis", blocked)        # exclusive to mis_builder -> gated
        self.assertNotIn("report", blocked)  # shared PDF infra -> NEVER gated

    def test_enterprise_wrapper_module_does_not_block_its_dependency(self):
        """#240 regression: the Enterprise plan licenses mis_builder via the
        WRAPPER `ncollection_mis_templates` (the literal in the plan), not
        `mis_builder` itself. The dependency closure must keep mis_builder
        licensed — else EVERY Enterprise tenant would lose MIS reporting."""
        if "mis.report" not in self.env:
            self.skipTest("mis_builder not installed in this run")
        # the real Enterprise allowed_module_names value (wrapper, not mis_builder)
        self._set_plan("account,account_financial_report,ncollection_mis_templates")
        blocked = self.Menu.sudo()._ncollection_blocked_module_names()
        self.assertNotIn("mis_builder", blocked)  # licensed via the wrapper's deps
        self.assertNotIn("mis", self.Menu.sudo()._ncollection_blocked_namespaces_cached())

    def test_downgrade_gates_afr_model_in_shared_namespace(self):
        """#240 (isolation+security HIGH): account_financial_report defines
        `account.age.report.configuration` in the SHARED `account` namespace, so
        namespace gating can't touch it (blocking `account` would break core
        Accounting). Model-level gating denies it on downgrade while leaving core
        `account.move` licensed."""
        if "account.age.report.configuration" not in self.env:
            self.skipTest("account_financial_report not installed in this run")
        self._set_plan("account")  # account licensed, account_financial_report not
        blocked_models = self.Menu.sudo()._ncollection_blocked_models_cached()
        self.assertIn("account.age.report.configuration", blocked_models)
        self.assertNotIn("account.move", blocked_models)  # core account NOT gated
        # the AFR model carries base.group_user ACL, so a non-system user passes
        # core's check and hits OUR license denial:
        with self.assertRaises(AccessError) as cm:
            self.env["account.age.report.configuration"].with_user(
                self.user).check_access("read")
        self.assertIn("NCollection plan", str(cm.exception))

    def test_derivation_error_fails_open(self):
        """A crash anywhere in the blocked-set derivation must DISABLE
        enforcement (fail-open), never brick the tenant with 500s (#240).

        The fail-open path logs CRITICAL *with a traceback* (exc_info) — real
        diagnostics for a silently-disabled license. assertLogs both proves that
        log fired AND disables propagation for its duration, so the expected
        traceback never reaches the shared odoo-test.log; otherwise the CI
        'no traceback in log' gate would flag our own intentional traceback.
        """
        self._set_plan("crm")
        with patch.object(type(self.Menu), "_ncollection_compute_blocked_access",
                          side_effect=RuntimeError("boom")):
            self.env.registry.clear_cache()
            with self.assertLogs(le._logger, level="CRITICAL"):
                # first (uncached) call triggers the compute -> raises -> logs;
                # the empty result is cached, so later calls are silent hits.
                self.assertEqual(
                    self.Menu._ncollection_blocked_namespaces_cached(), frozenset())
            self.assertEqual(self.Menu._ncollection_blocked_models_cached(), frozenset())
            # enforcement disabled -> a normally-gateable access is NOT denied
            self.env["res.partner"].with_user(self.user).check_access("read")
        self.env.registry.clear_cache()  # restore real derivation for later tests

    def test_blocked_module_names_error_fails_open(self):
        """A crash in _ncollection_blocked_module_names (e.g. the dependency
        walk) also fails open -> empty set, not a raised 500 (#240).

        assertLogs captures the expected CRITICAL+traceback (see the sibling
        test) so it stays out of the shared log and the CI gate stays honest.
        """
        self._set_plan("crm")
        with patch.object(type(self.Menu), "_ncollection_expand_dependencies",
                          side_effect=RuntimeError("boom")):
            with self.assertLogs(iuim._logger, level="CRITICAL"):
                self.assertEqual(
                    self.Menu.sudo()._ncollection_blocked_module_names(), set())

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
