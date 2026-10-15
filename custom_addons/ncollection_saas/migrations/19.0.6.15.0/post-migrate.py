# -*- coding: utf-8 -*-
"""Repair the stored portal_url of tenants provisioned with the retired dev template (see
ncollection.tenant._nc_repair_legacy_portal_urls: only that exact value, only via the one URL builder)."""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    fixed = env['ncollection.tenant']._nc_repair_legacy_portal_urls()
    _logger.info("ncollection_saas: repaired the stored portal_url of %s tenant(s).", len(fixed))
