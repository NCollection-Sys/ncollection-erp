# -*- coding: utf-8 -*-
import logging

from odoo import SUPERUSER_ID, api, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# The reserved login of the platform maintenance administrator (see _nc_ensure_maintenance_admin). No '@', no
# 'platform.' prefix (that one belongs to the entry proxies of named platform administrators).
MAINTENANCE_LOGIN = '__nc_platform_maintenance__'


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
        # BEFORE any Owner loses the technical groups: Odoo refuses every later user write while base.group_system has
        # no ACTIVE member, and in a new tenant the `admin` Owner is its only one (the superuser is archived).
        self._nc_ensure_maintenance_admin()
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

    @api.model
    def _nc_ensure_maintenance_admin(self):
        """The tenant's platform MAINTENANCE administrator: the one active holder of base.group_system Odoo requires.

        Odoo 19 (``res.users._check_at_least_one_administrator``) refuses any user create or group change while
        ``base.group_system`` has no ACTIVE member. Under Option A no Owner holds it, and the superuser is archived, so
        without this account a tenant cannot create a user at all - provisioning failed at its seed for exactly that
        reason (the config-sync service account is a user create).

        It is a platform account (``nc_platform_proxy``) with NO ``nc_platform_ref``, so it can NEVER sign in:
        ``_check_credentials`` refuses every password for a proxy, and an entry grant needs a ref. It holds
        no password, no email (no reset link can reach it), no API key and no customer role. As a proxy it is not a
        customer seat, it is hidden from workspace user lists and the administration service, and only the
        superuser can create or change it. Its single purpose is to satisfy the invariant. Idempotent;
        reactivates and repairs the account if it was archived or stripped. Returns the account.
        """
        env = api.Environment(self.env.cr, SUPERUSER_ID, {'active_test': False, 'no_reset_password': True})
        Users = env['res.users']
        system = env.ref('base.group_system')
        user = Users.search([('login', '=', MAINTENANCE_LOGIN)], limit=1)
        if user and not user.nc_platform_proxy:
            # a real account squatting the reserved login: never promote it
            raise UserError(self.env._("The login '%s' is reserved for the platform maintenance account.",
                                       MAINTENANCE_LOGIN))
        if not user:
            company = env.ref('base.main_company', raise_if_not_found=False) or env['res.company'].search([], limit=1)
            user = Users.create({
                'name': env._('Platform Maintenance (system)'), 'login': MAINTENANCE_LOGIN,
                'nc_platform_proxy': True, 'company_id': company.id, 'company_ids': [(6, 0, company.ids)],
                'group_ids': [(6, 0, [system.id])]})
            _logger.info("Platform maintenance administrator created (id %s).", user.id)
            return user
        vals = {}
        if not user.active:
            vals['active'] = True
        if system not in user.group_ids:
            vals['group_ids'] = [(4, system.id)]
        if user.nc_platform_ref:
            vals['nc_platform_ref'] = False
        if vals:
            user.write(vals)
            _logger.info("Platform maintenance administrator repaired: %s.", ', '.join(sorted(vals)))
        return user

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
            from odoo.addons.ncollection_core.hooks import _sync_role_implications, _sync_scheduler_read_access
            # In a SAVEPOINT: after a module install every worker reloads its registry at once and runs this; a
            # concurrent-update failure must roll back only this sync. Without it the swallowed error left the whole
            # transaction aborted, the next registry step failed, and that request answered HTTP 500 (production,
            # 2026-11-18, the platform install of mass_mailing).
            with self.env.cr.savepoint():
                _sync_role_implications(self.env)
                # the scheduler's read-only ACLs on the apps installed since core (kept here when Anomaly Alerts went)
                _sync_scheduler_read_access(self.env)
        except Exception:                                    # pragma: no cover
            # a registry that fails to load is an outage; a missing link is a gap
            _logger.exception("ncollection_core: syncing role implications failed")
        return result
