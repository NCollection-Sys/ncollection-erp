# -*- coding: utf-8 -*-
"""Retire the built-in production base domain and the two competing URL templates.

Before this version the module SEEDED ``ncollection_saas.base_domain = ncollectionerp.com`` into every database,
including a developer's, so a local workspace advertised itself as ``<name>.ncollectionerp.com``; and two more
parameters (``entry_url_template``, ``portal_url_template``) each decided a different URL for the same tenant. The base
domain is now configured once (SaaS Settings, else the NC_BASE_DOMAIN environment) and every address is built from it.

What this removes, and only this:
  * the SEEDED base domain, when nobody ever edited it (``write_date`` equals ``create_date``) AND the deployment
    states its domain via NC_BASE_DOMAIN. A value an operator set deliberately, or any value on a deployment that does
    not yet declare NC_BASE_DOMAIN, is left exactly as it is: removing it would change production behaviour silently.
  * the two template parameters, which nothing reads any more.
Idempotent: a second run finds nothing to remove.
"""
import logging
import os

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    if (os.environ.get('NC_BASE_DOMAIN') or '').strip():
        cr.execute("""
            DELETE FROM ir_config_parameter
             WHERE key = 'ncollection_saas.base_domain' AND value = 'ncollectionerp.com'
               AND write_date IS NOT DISTINCT FROM create_date
        """)
        _logger.info("ncollection_saas: removed the unedited seeded base domain (%s row(s)).", cr.rowcount)
    else:
        _logger.warning("ncollection_saas: NC_BASE_DOMAIN is not set in this environment; the seeded base domain "
                        "was left in place. Set NC_BASE_DOMAIN (see docs/markdown/TENANT_URLS.md).")
    cr.execute("DELETE FROM ir_config_parameter WHERE key IN "
               "('ncollection_saas.entry_url_template', 'ncollection_saas.portal_url_template')")
    cr.execute("""
        DELETE FROM ir_model_data
         WHERE module = 'ncollection_saas' AND model = 'ir.config_parameter'
           AND name IN ('param_portal_url_template', 'param_base_domain')
           AND res_id NOT IN (SELECT id FROM ir_config_parameter)
    """)
