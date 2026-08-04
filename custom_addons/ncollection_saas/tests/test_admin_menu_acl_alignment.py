# -*- coding: utf-8 -*-
"""#245: the SaaS-admin menus must agree with the ACLs behind them.

Standing Rule 4 says a UI restriction is mirrored at the ORM layer. The reverse
matters too: an ORM restriction with no matching UI restriction produces a menu
that is visible and then refuses to open.

Before this, `ncollection.backup` / `ncollection.domain` /
`ncollection.fleet.migration` were ACL-gated to `base.group_system` while their
menus inherited the root's `group_platform_admin`. Since `group_system` implies
`group_platform_admin` and not the reverse, a platform-admin-only user SAW all
three menus and got `AccessError` on opening any of them.

Aligned DOWN, to `group_system` on the menus. The other direction — widening the
ACL to `group_platform_admin`, as the issue originally suggested — would have
handed platform-admins real access to tenant backups, DNS/SSL records and
fleet-wide migrations that they do not have today. Never widen a boundary to
resolve an inconsistency.
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
        """The acceptance: menu visibility and ORM access give the same answer.

        Asserted as a pair per surface, so a future edit that re-opens the menu
        without opening the ACL (or vice versa) fails here rather than becoming
        a support ticket about a menu that errors on click.
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
