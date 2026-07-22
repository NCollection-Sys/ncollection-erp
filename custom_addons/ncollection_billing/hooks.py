# -*- coding: utf-8 -*-
"""Post-install setup for admin-DB subscription billing (P2-T11).

The platform (admin) DB must be able to issue customer invoices for tenant
subscriptions. That needs a Chart of Accounts, a UAE VAT tax, and a billing
product. This hook provisions them idempotently, loading Odoo's built-in
generic chart template when the company has none (approved decision A) — no new
localization module dependency.
"""
import logging

_logger = logging.getLogger(__name__)


def post_init_hook(env):
    company = env.ref('base.main_company', raise_if_not_found=False) or env.company
    if not company:
        return
    company._nc_ensure_billing_setup()
    _logger.info("ncollection_billing: billing setup ensured for company %s", company.name)
