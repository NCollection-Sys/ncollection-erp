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
    changed = env['res.groups']._nc_enforce_owner_least_privilege()
    _logger.info("Option A migration: %s Owner account(s) changed.", len(changed))
