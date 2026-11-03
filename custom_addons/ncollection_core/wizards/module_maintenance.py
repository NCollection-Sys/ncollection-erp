# -*- coding: utf-8 -*-
"""The module-maintenance wizards (Update Apps List, Apply Scheduled Upgrades) take the same module-management
lock as the buttons of ir.module.module (models/ir_module.py): module management belongs to the platform."""
from odoo import models


class BaseModuleUpgrade(models.TransientModel):
    """Apply Scheduled Upgrades: rebuilds the registry with `update_module=True`, which applies every queued module
    state and runs the upgrade of every module marked for it. Not a button on a module, so not covered above."""
    _inherit = 'base.module.upgrade'

    def upgrade_module(self):
        self.env['ir.module.module']._nc_assert_module_management_allowed('base.module.upgrade')
        return super().upgrade_module()

    def upgrade_module_cancel(self):
        self.env['ir.module.module']._nc_assert_module_management_allowed('base.module.upgrade:cancel')
        return super().upgrade_module_cancel()


class BaseModuleUpdate(models.TransientModel):
    """Update Apps List, the wizard (it calls `update_list`; refused before the wizard reports anything)."""
    _inherit = 'base.module.update'

    def update_module(self):
        self.env['ir.module.module']._nc_assert_module_management_allowed('base.module.update')
        return super().update_module()
