# -*- coding: utf-8 -*-
"""#245: keep the SaaS-admin ACL boundary where it is.

**The bug #245 describes does not exist.** It reports that a
`group_platform_admin`-only user SEES the Tenant Backups / Domains & SSL /
Fleet Migrations menus and then gets `AccessError` on opening them, because
those models are ACL-gated to `base.group_system` while the menus inherited the
root's `group_platform_admin`.

Measured on a fresh database with the menu groups deliberately absent — the
exact pre-#245 state — the menus were **already hidden**. Odoo's
`_visible_menu_ids()` excludes any `act_window`-backed menu whose target model
the user cannot read, and it does so *before* the menu's own `group_ids` are
consulted. There is no visible-then-errors state to fix.

So the `groups="base.group_system"` on those menuitems is **redundant today**.
It is kept to state the intent explicitly rather than lean on Odoo's implicit
action-based filtering, which is behaviour that could change.

**What these tests actually guard** is the opposite direction. #245 proposes
aligning "likely `group_platform_admin` for both" — which WIDENS privilege,
since `group_system` implies `group_platform_admin` and not the reverse. That
would hand platform-admins live access to tenant backups, DNS/SSL records and
fleet-wide migrations. RED-proved: applying it fails every subtest below with
"AccessError not raised".

Read that asymmetry carefully before editing: the `AccessError` assertions carry
all the regression-detection weight. The menu-visibility assertions re-confirm
Odoo's own action-based filter and would hold even with the `groups=` removed.
"""
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged

# (menu xmlid, model) — the three surfaces #245 names.
_ADMIN_SURFACES = [
    ('ncollection_saas.menu_ncollection_saas_backups', 'ncollection.backup'),
    ('ncollection_saas.menu_ncollection_saas_domains', 'ncollection.domain'),
    ('ncollection_saas.menu_ncollection_saas_fleet_migration',
     'ncollection.fleet.migration'),
]


@tagged("post_install", "-at_install")
class TestAdminMenuAclAlignment(TransactionCase):

    def setUp(self):
        super().setUp()
        # A platform admin who is NOT a system admin — the exact user the
        # mismatch stranded.
        self.platform_admin = self.env['res.users'].create({
            'name': 'Platform Admin Only', 'login': 'nc_platform_admin_only',
            'group_ids': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref('ncollection_subscription.group_platform_admin').id,
            ])],
        })

    def _visible_menu_ids(self, user):
        """Menu ids `user` can actually see.

        MUST filter a NON-EMPTY recordset: `_filter_visible_menus()` filters
        `self`, so calling it on `env['ir.ui.menu']` returns an empty set and
        every `assertNotIn` against it passes vacuously. That bit me while
        writing this file — the first version "proved" the menus were hidden
        from a platform admin when it had proved nothing at all.
        """
        menus = self.env['ir.ui.menu'].with_user(user).search([])
        self.assertTrue(menus, "no menus at all — the check would be vacuous")
        return menus._filter_visible_menus().ids

    def test_platform_admin_sees_no_menu_they_cannot_open(self):
        """Menu visibility and ORM access must give the same answer.

        The two halves do NOT carry equal weight, and it matters:

        * ``assertRaises(AccessError)`` is the real guard. Widen the ACL to
          ``group_platform_admin`` — the change #245 proposes — and this fails.
        * ``assertNotIn(menu, visible)`` re-confirms Odoo's own action-based
          filtering. It holds even with the ``groups=`` this branch added
          removed, so it cannot detect a regression in that attribute.

        Kept as a pair anyway: together they state the invariant a reader needs
        (these surfaces are system-admin only, at both layers), and the pairing
        is what would catch a future edit that opened one layer but not the
        other.
        """
        visible = self._visible_menu_ids(self.platform_admin)
        for menu_xmlid, model_name in _ADMIN_SURFACES:
            with self.subTest(menu=menu_xmlid):
                self.assertNotIn(
                    self.env.ref(menu_xmlid).id, visible,
                    "%s is visible to a platform-admin who cannot open it"
                    % menu_xmlid)
                with self.assertRaises(AccessError):
                    self.env[model_name].with_user(
                        self.platform_admin).check_access('read')

    def test_a_system_admin_still_sees_all_three(self):
        """Narrowing the menus must not lock out the people who own them."""
        system_admin = self.env['res.users'].create({
            'name': 'System Admin', 'login': 'nc_system_admin_probe',
            'group_ids': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref('base.group_system').id,
            ])],
        })
        visible = self._visible_menu_ids(system_admin)
        for menu_xmlid, model_name in _ADMIN_SURFACES:
            with self.subTest(menu=menu_xmlid):
                self.assertIn(
                    self.env.ref(menu_xmlid).id, visible,
                    "%s is hidden from a system admin" % menu_xmlid)
                # ...and the model genuinely opens for them.
                self.env[model_name].with_user(system_admin).check_access('read')

    def test_the_root_menu_itself_is_unchanged(self):
        """Scope guard: #245 aligns three leaf menus. The SaaS root stays on
        group_platform_admin — other surfaces under it (e.g. Resellers, whose
        ACL really is group_platform_admin) are already consistent and must not
        be dragged along."""
        root = self.env.ref('ncollection_subscription.menu_ncollection_saas_root')
        # Odoo 19 renamed this field: group_ids, not groups_id.
        self.assertIn(
            self.env.ref('ncollection_subscription.group_platform_admin'),
            root.group_ids)
