# -*- coding: utf-8 -*-
import logging

from odoo import SUPERUSER_ID, api, models

_logger = logging.getLogger(__name__)


class ResGroups(models.Model):
    _inherit = 'res.groups'

    @api.model
    def _nc_enforce_owner_least_privilege(self):
        """Option A, enforced: the workspace Owner is never a platform technical administrator.

        1. The Owner role no longer implies base.group_system / base.group_erp_manager (a database created before
           Option A stores that implication, and the role definition's data alone has been seen to leave it).
        2. No Owner holds them directly either - Odoo's base gives them to the `admin` account itself - active or
           archived (an archived Owner reactivated later must not come back with them).
        The platform's entry proxy keeps them (platform staff, maintenance), and the platform database is never
        touched: it has the platform administrator group, a tenant never does. Idempotent; returns the logins changed.
        """
        owner = self.env.ref('ncollection_core.group_role_owner', raise_if_not_found=False)
        if not owner or self.env.ref('ncollection_subscription.group_platform_admin', raise_if_not_found=False):
            return []
        technical = self.browse([g.id for g in (self.env.ref('base.group_system', raise_if_not_found=False),
                                                self.env.ref('base.group_erp_manager', raise_if_not_found=False))
                                 if g]).sudo()
        implied = owner.sudo().implied_ids & technical
        if implied:
            owner.sudo().write({'implied_ids': [(3, group.id) for group in implied]})
            _logger.info("Option A: the Owner role no longer implies %s.", ', '.join(implied.mapped('name')))
        changed = []
        owners = self.env['res.users'].sudo().with_context(active_test=False).search([
            ('all_group_ids', 'in', owner.id), ('nc_platform_proxy', '=', False), ('id', '!=', SUPERUSER_ID)])
        for user in owners:
            direct = user.group_ids & technical
            if direct:
                user.write({'group_ids': [(3, group.id) for group in direct]})
                changed.append(user.login)
                _logger.info("Option A: removed %s from workspace Owner %s (%s).",
                             ', '.join(direct.mapped('name')), user.id, user.login)
        return changed

    def _register_hook(self):
        """Keep the role -> app-group links in step with the installed apps.

        The roles imply groups that belong to OTHER modules (accounting, sales,
        ...). Those links can only be made once those modules exist, and the
        post_init hook / provisioning seed makes them once. A tenant provisioned
        before an app was installed (or by an older seed) therefore kept an
        Owner who could not use that app. Re-running on every registry load is
        the R-014 remedy: idempotent, and a no-op once the links exist.
        """
        result = super()._register_hook()
        try:
            from odoo.addons.ncollection_core.hooks import _sync_role_implications
            _sync_role_implications(self.env)
        except Exception:                                    # pragma: no cover
            # a registry that fails to load is an outage; a missing link is a gap
            _logger.exception("ncollection_core: syncing role implications failed")
        return result
