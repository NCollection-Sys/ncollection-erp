# -*- coding: utf-8 -*-
"""Post-install setup for admin-DB subscription billing (P2-T11 / P2-T13).

The platform (admin) DB must be able to issue customer invoices for tenant
subscriptions. That needs a Chart of Accounts, a UAE VAT tax, and a billing
product. This hook provisions them idempotently, loading Odoo's built-in
generic chart template when the company has none (approved decision A) — no new
localization module dependency.

P2-T13 also configures Odoo's core Stripe provider (admin DB) from environment
secrets, in TEST mode, so subscription invoices can be paid online. Keys are
NEVER stored in git (ARCHITECTURE_SECURITY §7): they come from NC_STRIPE_* env
vars, and the provider stays disabled when they are absent. Going live (live
keys, state='enabled') is a deliberate ops action, not a module install.
"""
import logging
import os

_logger = logging.getLogger(__name__)

STRIPE_SECRET_ENV = 'NC_STRIPE_SECRET_KEY'
STRIPE_PUBLISHABLE_ENV = 'NC_STRIPE_PUBLISHABLE_KEY'
STRIPE_WEBHOOK_ENV = 'NC_STRIPE_WEBHOOK_SECRET'


def _configure_stripe(env):
    """Enable the seeded Stripe provider in TEST mode from env secrets.

    No-op (provider stays disabled) unless both the secret and publishable keys
    are present, so a stack without Stripe configured simply has no online
    payment — never a half-configured, broken provider."""
    secret = os.environ.get(STRIPE_SECRET_ENV)
    publishable = os.environ.get(STRIPE_PUBLISHABLE_ENV)
    if not (secret and publishable):
        _logger.info(
            "ncollection_billing: %s/%s not set — Stripe stays disabled (no online payment)",
            STRIPE_SECRET_ENV, STRIPE_PUBLISHABLE_ENV)
        return
    provider = env.ref('payment.payment_provider_stripe', raise_if_not_found=False)
    if not provider:
        _logger.warning("ncollection_billing: Stripe provider record not found — skipping")
        return
    vals = {
        'stripe_secret_key': secret,
        'stripe_publishable_key': publishable,
        'state': 'test',   # TEST mode only; ops flips to 'enabled' with live keys
    }
    webhook_secret = os.environ.get(STRIPE_WEBHOOK_ENV)
    if webhook_secret:
        vals['stripe_webhook_secret'] = webhook_secret
    provider.write(vals)
    # Let customers pay their subscription invoices from the portal.
    env['ir.config_parameter'].sudo().set_param('account_payment.enable_portal_payment', 'True')
    _logger.info("ncollection_billing: Stripe provider configured in TEST mode (P2-T13)")


def post_init_hook(env):
    company = env.ref('base.main_company', raise_if_not_found=False) or env.company
    if not company:
        return
    company._nc_ensure_billing_setup()
    _configure_stripe(env)
    _logger.info("ncollection_billing: billing setup ensured for company %s", company.name)
