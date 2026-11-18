# -*- coding: utf-8 -*-
"""Odoo's administrator invariant on tenants already under Option A.

Odoo 19 refuses any user create or group change while ``base.group_system`` has no ACTIVE member. Option A took it
from every Owner, and the superuser is archived, so a tenant with no platform entry proxy could no longer create a
user (measured on a production copy). The shared enforcement now ensures the passwordless, non-interactive platform
maintenance administrator first (``res.groups._nc_ensure_maintenance_admin``). Idempotent; never runs on the platform
database (the enforcement returns there before doing anything).
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    from odoo import api, SUPERUSER_ID
    env = api.Environment(cr, SUPERUSER_ID, {})
    changed = env['res.groups']._nc_enforce_owner_least_privilege()
    _logger.info("Administrator invariant (tenant databases only; the platform is skipped): "
                 "%s Owner account(s) changed.", len(changed))
