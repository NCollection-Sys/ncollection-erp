# -*- coding: utf-8 -*-
from odoo import api, models


class ResUsers(models.Model):
    _inherit = 'res.users'

    @api.model
    def _nc_actor_may_grant_system_branding(self):
        """Platform staff may give the System Branding Manager right (ncollection_branding's grant guard): in the
        platform database every Platform Admin is staff, and Platform Admin implies that right anyway, so making
        someone a platform operator keeps working. This module is never installed in a tenant database, so a tenant
        Owner never reaches this branch."""
        if super()._nc_actor_may_grant_system_branding():
            return True
        return bool(self.env.uid and self.env.user.has_group('ncollection_subscription.group_platform_admin'))
