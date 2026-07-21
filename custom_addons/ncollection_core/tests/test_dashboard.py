# -*- coding: utf-8 -*-
"""Customer Workspace Dashboard — data service tests (P1-T17).

The two properties worth protecting here are the ones that would fail silently:

1. **Role gating is server-side.** A widget a role may not see must be ABSENT
   from the payload, not merely hidden by the client (Standing Rule 4). These
   tests assert on the payload, never on rendered markup.
2. **Dependencies are soft.** A provider whose model is not installed drops its
   widget instead of raising — that is what lets a CRM-only tenant land on this
   dashboard without Sales or Accounting installed.
"""

from odoo.tests import TransactionCase, tagged

from odoo.addons.ncollection_core.models.dashboard import dashboard_data as dash


@tagged("post_install", "-at_install")
class TestDashboardRoleGating(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Dashboard = cls.env["ncollection.dashboard.data"]

    def _user_with_role(self, role_xmlid):
        """A fresh internal user holding exactly one NCollection role."""
        group = self.env.ref(role_xmlid)
        user = self.env["res.users"].create({
            "name": f"probe {role_xmlid}",
            "login": f"probe_{role_xmlid.split('.')[-1]}",
            "group_ids": [(6, 0, [self.env.ref("base.group_user").id, group.id])],
        })
        return user

    def test_every_role_resolves_to_its_documented_widget_groups(self):
        """All 8 roles map exactly as demo/src/lib/roles.ts specifies.

        Resolution is a UNION over held groups, which matters because the roles
        declare implied_ids (owner -> ceo -> manager -> employee). The expected
        values below are the demo's per-role sets; if the union over-granted,
        these would not match.
        """
        expected = {
            "ncollection_core.group_role_employee": {dash.GROUP_PERSONAL},
            "ncollection_core.group_role_hr": {dash.GROUP_PERSONAL},
            "ncollection_core.group_role_sales": {dash.GROUP_PIPELINE, dash.GROUP_PERSONAL},
            "ncollection_core.group_role_warehouse": {dash.GROUP_OPERATIONS, dash.GROUP_PERSONAL},
            "ncollection_core.group_role_accountant": {dash.GROUP_FINANCIAL, dash.GROUP_PERSONAL},
            "ncollection_core.group_role_manager": {
                dash.GROUP_PIPELINE, dash.GROUP_OPERATIONS, dash.GROUP_PERSONAL,
            },
            "ncollection_core.group_role_ceo": set(dash.ALL_WIDGET_GROUPS),
            "ncollection_core.group_role_owner": set(dash.ALL_WIDGET_GROUPS),
        }
        for role_xmlid, wanted in expected.items():
            user = self._user_with_role(role_xmlid)
            got = self.Dashboard.with_user(user)._widget_groups_for_user()
            self.assertEqual(
                got, wanted,
                f"{role_xmlid} resolved to {sorted(got)}, expected {sorted(wanted)}",
            )

    def test_additive_roles_union_rather_than_override(self):
        """Roles are additive: Accountant + Sales sees both widget families."""
        user = self._user_with_role("ncollection_core.group_role_accountant")
        user.write({
            "group_ids": [(4, self.env.ref("ncollection_core.group_role_sales").id)],
        })
        groups = self.Dashboard.with_user(user)._widget_groups_for_user()
        self.assertIn(dash.GROUP_FINANCIAL, groups)
        self.assertIn(dash.GROUP_PIPELINE, groups)

    def test_denied_widget_is_absent_from_payload_not_merely_hidden(self):
        """Standing Rule 4: gating happens server-side.

        A role without the 'personal' group must not receive the personal
        widget at all. Asserted against the payload, because a client-side-only
        filter would still pass a markup test.
        """
        user = self._user_with_role("ncollection_core.group_role_employee")
        service = self.Dashboard.with_user(user)

        # Force a provider into a group this user does not hold.
        specs = [dict(service._provider_specs()[0], group=dash.GROUP_FINANCIAL)]
        with self._patched_specs(service, specs):
            payload = service.get_dashboard_payload()

        keys = [w["key"] for w in payload["widgets"]]
        self.assertNotIn(
            "open_activities", keys,
            "a widget outside the user's widget groups leaked into the payload",
        )

    def test_provider_is_dropped_when_its_model_is_not_installed(self):
        """Soft dependency: an absent app removes its widget, without raising."""
        user = self._user_with_role("ncollection_core.group_role_owner")
        service = self.Dashboard.with_user(user)

        specs = [dict(service._provider_specs()[0], model="module.that.is.not.installed")]
        with self._patched_specs(service, specs):
            payload = service.get_dashboard_payload()

        self.assertEqual(
            payload["widgets"], [],
            "widget survived despite its backing model being absent",
        )

    def test_payload_shape_and_first_widget_end_to_end(self):
        """The real provider runs and returns a renderable widget.

        mail.activity is not a dependency of ncollection_core, so this also
        exercises the HAPPY path of the soft-dependency check whenever mail is
        installed (it is, via sibling modules and on every real tenant).
        """
        user = self._user_with_role("ncollection_core.group_role_owner")
        payload = self.Dashboard.with_user(user).get_dashboard_payload()

        self.assertIn("widgets", payload)
        self.assertIn("meta", payload)
        self.assertEqual(payload["meta"]["user_name"], user.name)
        self.assertIn("widget_groups", payload["meta"])

        if "mail.activity" in self.env:
            widget = next(w for w in payload["widgets"] if w["key"] == "open_activities")
            self.assertEqual(widget["group"], dash.GROUP_PERSONAL)
            self.assertIsInstance(widget["value"], int)
            self.assertTrue(widget["label"])

    # -- helpers ------------------------------------------------------------

    def _patched_specs(self, service, specs):
        """Context manager swapping the provider registry for one call."""
        model_cls = type(service)
        original = model_cls._provider_specs

        class _Patch:
            def __enter__(self_inner):
                model_cls._provider_specs = lambda self, *a, **kw: specs

            def __exit__(self_inner, *exc):
                model_cls._provider_specs = original
                return False

        return _Patch()
