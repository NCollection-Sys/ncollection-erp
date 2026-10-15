# -*- coding: utf-8 -*-
"""Move this database's web.base.url off the retired dev template (see
ncollection.workspace.config._nc_repair_legacy_base_url: only that exact value, only when NC_BASE_DOMAIN is set)."""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    url = env['ncollection.workspace.config']._nc_repair_legacy_base_url()
    _logger.info("ncollection_core: web.base.url %s.", ("moved to %s" % url) if url else "left as it was")
