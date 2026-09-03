# -*- coding: utf-8 -*-
"""A tenant may not manage its own Odoo modules (#459).

THE DEFECT. A Wasla tenant user opened `/odoo/apps` and installed CRM — a
module the platform had not sold them. It worked because the provisioning seed
makes the tenant admin the workspace OWNER and `group_role_owner` implies
`base.group_system`, which is exactly the profile Odoo lets manage modules.

So these tests use a user WITH `base.group_system`. Testing an ordinary user
would prove nothing: ordinary users were never able to do this, and the whole
point is that the privileged tenant user cannot either.

The platform's own install path is not exercised here because it does not use
these methods at all — `odoo -i` in a subprocess goes through the module
loader. What IS asserted is that superuser code still passes the guard, which
is the property that path relies on.
"""
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged

from odoo.addons.ncollection_core.models import ir_module as ir_module_mod


@tagged('post_install', '-at_install')
class TestTenantModuleLockout(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Module = cls.env['ir.module.module']
        # A tenant OWNER: system rights inside their own workspace, which is
        # what the seed produces and what made the bypass reachable.
        cls.owner = cls.env['res.users'].create({
            'name': 'Tenant Owner', 'login': 'nc_modlock_owner',
            'group_ids': [(6, 0, [
                cls.env.ref('base.group_user').id,
                cls.env.ref('base.group_system').id,
            ])],
        })
        cls.target = cls.Module.search([('state', '=', 'uninstalled')], limit=1)

    def test_the_probe_user_really_is_privileged(self):
        """The control. If this user were not a system user, every refusal
        below would be Odoo's own ACL talking and would prove nothing about
        #459."""
        self.assertTrue(self.owner.has_group('base.group_system'))

    def test_a_tenant_owner_cannot_install_a_module(self):
        """THE BYPASS, PINNED. This is the exact operation the Apps screen
        performs when 'Activate' is clicked."""
        if not self.target:
            self.skipTest("no uninstalled module available in this database")
        with self.assertRaises(AccessError):
            self.target.with_user(self.owner).button_immediate_install()

    def test_every_module_management_entry_point_is_blocked(self):
        """Blocking the obvious button is not enough: Odoo exposes install,
        upgrade and uninstall in both queued and immediate forms, plus a
        wizard and a state reset. A gap in any of them is the whole hole
        again, so the inventory is asserted rather than trusted."""
        module = self.target or self.Module.search([], limit=1)
        blocked = module.with_user(self.owner)
        for operation in ir_module_mod._BLOCKED_MODULE_OPERATIONS:
            with self.assertRaises(AccessError, msg=operation):
                getattr(blocked, operation)()

    def test_the_blocked_inventory_matches_what_odoo_actually_exposes(self):
        """A rename upstream must fail loudly here rather than silently leave
        a route unguarded — the failure mode a hardcoded list invites."""
        for operation in ir_module_mod._BLOCKED_MODULE_OPERATIONS:
            self.assertTrue(
                hasattr(self.Module, operation),
                "ir.module.module.%s no longer exists — the guard for it is "
                "now dead code, and Odoo may have renamed the route it "
                "protected." % operation)

    def test_writing_the_state_field_directly_is_blocked(self):
        """`state` is what an install actually changes; a direct write is the
        same operation with the buttons skipped."""
        module = self.target or self.Module.search([], limit=1)
        with self.assertRaises(AccessError):
            module.with_user(self.owner).write({'state': 'to install'})

    def test_creating_module_rows_is_blocked(self):
        """Otherwise a tenant could manufacture an 'installed' row."""
        with self.assertRaises(AccessError):
            self.Module.with_user(self.owner).create({
                'name': 'nc_fake_module', 'state': 'installed'})

    def test_the_settings_page_path_is_covered_by_the_same_guard(self):
        """res.config.settings' `module_<name>` booleans install through
        button_immediate_install / button_immediate_uninstall, so the Settings
        page is not a second door — it is the same door. Asserted explicitly
        because a future guard scoped to "the Apps action" would pass every
        other test in this file and leave this open."""
        module = self.target or self.Module.search([], limit=1)
        for operation in ('button_immediate_install', 'button_immediate_uninstall'):
            with self.assertRaises(AccessError, msg=operation):
                getattr(module.with_user(self.owner), operation)()

    def test_platform_superuser_code_still_passes(self):
        """The escape hatch the platform's own install path depends on. Without
        this, the guard would be a self-inflicted outage: provisioning could no
        longer set up a tenant. `sudo()` reaches Odoo's real implementation —
        asserted by the error NOT being ours."""
        module = self.target or self.Module.search([], limit=1)
        try:
            module.sudo()._nc_assert_module_management_allowed('probe')
        except AccessError:
            self.fail("superuser must pass the module-management guard")
