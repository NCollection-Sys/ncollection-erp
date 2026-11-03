# -*- coding: utf-8 -*-
"""Option A migration: a tenant Owner is a business administrator, not a platform technical administrator.

Odoo's base grants the `admin` account (and historically our Owner role) ``base.group_system`` ->
``base.group_erp_manager`` DIRECTLY. That is the escape hatch that let a tenant Owner write server-action Python,
edit QWeb / record rules / groups / ACLs and become the superuser. The Owner role now implies
``group_tenant_admin`` instead, but the DIRECT memberships on existing Owner accounts must be removed too, or the
new role definition changes nothing for them.

Removes ``base.group_system`` and ``base.group_erp_manager`` from every workspace Owner that is not the platform's
entry proxy. Idempotent (a second run finds nothing to remove). Never runs on the platform database: if the
platform-admin group exists, this is the platform's own DB and its administrators keep their technical rights.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    from odoo import api, SUPERUSER_ID
    env = api.Environment(cr, SUPERUSER_ID, {})
    owner = env.ref('ncollection_core.group_role_owner', raise_if_not_found=False)
    platform = env.ref('ncollection_subscription.group_platform_admin', raise_if_not_found=False)
    if not owner or platform:
        # no Owner role, or this is the platform database -> nothing to migrate here
        return
    system = env.ref('base.group_system', raise_if_not_found=False)
    erp = env.ref('base.group_erp_manager', raise_if_not_found=False)
    strip = [g for g in (system, erp) if g]
    if not strip:
        return
    owners = env['res.users'].sudo().search([
        ('all_group_ids', 'in', owner.id), ('nc_platform_proxy', '=', False),
    ]).filtered(lambda u: u.id != SUPERUSER_ID)
    for user in owners:
        remove = [(3, g.id) for g in strip if g.id in user.group_ids.ids]
        if remove:
            user.write({'group_ids': remove})
            _logger.info("Option A: removed %s from workspace Owner %s (%s).",
                         ', '.join(g.name for g in strip), user.id, user.login)
