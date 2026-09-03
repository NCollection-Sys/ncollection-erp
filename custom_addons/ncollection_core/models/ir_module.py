# -*- coding: utf-8 -*-
"""Tenants may not manage their own Odoo modules (#459).

THE DEFECT THIS CLOSES. A tenant owner opened `/odoo/apps` on their own
workspace and installed CRM — a module the platform had not sold them. That is
a licensing bypass with a button on it, and it is reachable because the
provisioning seed makes the tenant admin the workspace OWNER, and
`ncollection_core.group_role_owner` implies `base.group_system`
(`role_groups.xml`). Ring 2 deliberately exempts system users
(`license_enforcement.py`), which is correct for licensing checks on business
models and exactly wrong for module management.

WHY NOT MENUS. `_OWNER_ONLY_MENUS` already hides Apps and Settings from
non-owners, and the owner still reached the screen by typing the URL. Menu
visibility is Ring 1 — presentation. The boundary has to be where the write
happens.

WHY THE SIBLING PATHS MATTER. `res.config.settings` ships `module_<name>`
boolean fields whose `set_values()` calls `button_immediate_install` /
`button_immediate_uninstall`. Blocking only the Apps screen would leave the
Settings page as an unguarded second door onto the same operation, which is why
this guards the MODEL METHODS rather than a screen.

WHAT STILL WORKS, and why it must. The platform installs a tenant's modules by
running `odoo -d <db> -i <modules>` in an isolated subprocess (provisioning, and
#459's install job). That path goes through Odoo's module LOADER, not through
these buttons, so it is unaffected by this guard — verified by the provisioning
suite, which installs real modules into real databases and stays green.

`self.env.su` is the one escape hatch: platform-side code that legitimately
drives a module operation runs as superuser, and `env.su` is not spoofable from
RPC (unlike a context key).
"""
import logging

from odoo import api, models
from odoo.exceptions import AccessError

_logger = logging.getLogger(__name__)

# Every entry point Odoo 19 exposes for installing, upgrading or removing a
# module (verified against base/models/ir_module.py, not assumed). Odoo splits
# these between "queue a state change" (button_install) and "do it now"
# (button_immediate_install); both are blocked, because the queued form is
# applied by the very next registry reload — blocking only the immediate one
# would leave a two-step path to the same result.
_BLOCKED_MODULE_OPERATIONS = (
    'button_install',
    'button_immediate_install',
    'button_upgrade',
    'button_immediate_upgrade',
    'button_uninstall',
    'button_immediate_uninstall',
    'button_uninstall_wizard',
    'button_reset_state',
    'module_uninstall',
)


class IrModuleModule(models.Model):
    _inherit = 'ir.module.module'

    def _nc_assert_module_management_allowed(self, operation):
        """Refuse module management to anyone inside a tenant workspace.

        Not `has_group`-based: EVERY tenant user is refused, owner included,
        because the owner is precisely who could do this. The only caller that
        passes is platform code running as superuser.
        """
        if self.env.su:
            return
        _logger.warning(
            "Refused module operation %r by user %s (%s) — module management "
            "belongs to the platform, not the tenant (#459).",
            operation, self.env.user.id, self.env.user.login)
        raise AccessError(self.env._(
            "Applications are managed by NCollection, not from inside your "
            "workspace. To add or remove an application, change your "
            "subscription plan — your workspace is updated for you.\n\n"
            "(Installing modules directly would bypass the plan your "
            "workspace is licensed under.)"))

    def write(self, vals):
        """`state` is what an install/upgrade actually changes, so a direct
        write on it is the same operation wearing a different hat."""
        if 'state' in vals:
            self._nc_assert_module_management_allowed('write:state')
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        # A tenant cannot invent module rows either — that is how a fake
        # "installed" state would be manufactured.
        self._nc_assert_module_management_allowed('create')
        return super().create(vals_list)

    # Written out rather than generated with setattr: Odoo builds the model
    # class from the registry, so a loop over `hasattr` at import time sees
    # none of these (they come from base's ir.module.module, not from this
    # Python class) and would have silently guarded NOTHING. Explicit
    # overrides also keep `super()` resolution obvious.
    #
    # _BLOCKED_MODULE_OPERATIONS above is the checked inventory: a test asserts
    # every name in it is overridden here AND still exists upstream, so an Odoo
    # rename fails loudly instead of leaving an unguarded route.
    def button_install(self):
        self._nc_assert_module_management_allowed('button_install')
        return super().button_install()

    def button_immediate_install(self):
        self._nc_assert_module_management_allowed('button_immediate_install')
        return super().button_immediate_install()

    def button_upgrade(self):
        self._nc_assert_module_management_allowed('button_upgrade')
        return super().button_upgrade()

    def button_immediate_upgrade(self):
        self._nc_assert_module_management_allowed('button_immediate_upgrade')
        return super().button_immediate_upgrade()

    def button_uninstall(self):
        self._nc_assert_module_management_allowed('button_uninstall')
        return super().button_uninstall()

    def button_immediate_uninstall(self):
        self._nc_assert_module_management_allowed('button_immediate_uninstall')
        return super().button_immediate_uninstall()

    def button_uninstall_wizard(self):
        self._nc_assert_module_management_allowed('button_uninstall_wizard')
        return super().button_uninstall_wizard()

    def button_reset_state(self):
        self._nc_assert_module_management_allowed('button_reset_state')
        return super().button_reset_state()

    def module_uninstall(self):
        self._nc_assert_module_management_allowed('module_uninstall')
        return super().module_uninstall()
