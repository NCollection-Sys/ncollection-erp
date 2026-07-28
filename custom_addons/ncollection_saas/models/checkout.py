# -*- coding: utf-8 -*-
"""Public self-service checkout (P2-T16) — tenant-side concerns.

Extends ncollection.tenant with the checkout/verification state and the pure
validation helpers the public controller relies on. Lives in ncollection_saas
(platform layer): checkout creates platform records (tenant, subscription,
provisioning job) and NEVER touches a tenant ERP model, so two-layer separation
holds.

Security (ARCHITECTURE_SECURITY §11 — the public checkout surface): provisioning
fires ONLY after email verification; subdomains are validated against a strict
grammar + reserved/offensive lists + a live-collision check; trial creation is
quota-limited per IP and per email; reCAPTCHA is verified when configured. The
Nginx edge rate-limits the routes (P1-T03) as the independent second layer.
"""

import logging
import re
import secrets

import requests

from odoo import fields, models

_logger = logging.getLogger(__name__)

# Subdomain === tenant key === database name: same grammar the provisioning
# engine enforces (^[a-z][a-z0-9]{2,62}$), so a chosen subdomain is a valid,
# routable, SQL-safe DB name.
SUBDOMAIN_RE = re.compile(r'^[a-z][a-z0-9]{2,62}$')

# Reserved platform/infra subdomains a tenant may never take.
RESERVED_SUBDOMAINS = frozenset({
    'admin', 'www', 'api', 'app', 'mail', 'smtp', 'imap', 'ftp', 'ns', 'ns1',
    'ns2', 'staging', 'stage', 'test', 'dev', 'demo', 'support', 'help',
    'billing', 'account', 'accounts', 'login', 'signup', 'signin', 'auth',
    'status', 'blog', 'docs', 'doc', 'cdn', 'static', 'assets', 'media',
    'root', 'postgres', 'db', 'sql', 'ncollection', 'nc', 'platform',
    'dashboard', 'portal', 'my', 'go', 'get', 'shop', 'store', 'pay',
})
# A short offensive-word blocklist (kept intentionally small in source).
OFFENSIVE_SUBDOMAINS = frozenset({'fuck', 'shit', 'porn', 'sex', 'nazi'})

_QUOTA_PER_IP_PARAM = 'ncollection_saas.trial_quota_per_ip'
_QUOTA_PER_EMAIL_PARAM = 'ncollection_saas.trial_quota_per_email'
_QUOTA_WINDOW_PARAM = 'ncollection_saas.trial_quota_window_hours'
_RECAPTCHA_SECRET_PARAM = 'ncollection_saas.recaptcha_secret'
_RECAPTCHA_VERIFY_URL = 'https://www.google.com/recaptcha/api/siteverify'


class TenantCheckout(models.Model):
    _inherit = 'ncollection.tenant'

    # Checkout/verification state. Kept off the base model (ncollection_subscription)
    # because email verification is a SaaS-layer public-onboarding concern.
    checkout_email_verified = fields.Boolean(default=False, copy=False)
    checkout_token = fields.Char(copy=False, index=True, groups='base.group_system')
    checkout_source_ip = fields.Char(copy=False, groups='base.group_system')

    # ---- subdomain validation / availability -----------------------------

    @staticmethod
    def _nc_normalize_subdomain(raw):
        return (raw or '').strip().lower()

    def _nc_subdomain_availability(self, subdomain, deep=True):
        """Return (available: bool, reason: str). reason in
        {'', 'invalid', 'reserved', 'taken'} — a stable key the UI translates.

        ``deep`` (default True) also probes the physical database cluster for an
        orphan DB — authoritative, used at register time. The public
        ``/nc/checkout/availability`` endpoint calls it with ``deep=False`` so a
        best-effort UX check stops at the tenant registry (a cheap ORM query) and
        never opens a psycopg2 connection per request: that endpoint is hammered,
        and the physical probe (``_database_exists``) opened a fresh connection
        every call (#226 M-1). register() still runs the full ``deep`` check, so
        an orphan-DB collision is caught authoritatively before provisioning.
        """
        subdomain = self._nc_normalize_subdomain(subdomain)
        if not SUBDOMAIN_RE.match(subdomain):
            return False, 'invalid'
        if subdomain in RESERVED_SUBDOMAINS or subdomain in OFFENSIVE_SUBDOMAINS:
            return False, 'reserved'
        # Collision space: an existing tenant, the live platform DB, or (deep
        # only) any database already in the cluster (a half-provisioned or
        # foreign DB).
        if subdomain == self.env.cr.dbname:
            return False, 'taken'
        if self.sudo().search_count([('database_name', '=', subdomain)]):
            return False, 'taken'
        if deep and self.env['ncollection.provisioning.job'].sudo()._database_exists(subdomain):
            return False, 'taken'
        return True, ''

    # ---- abuse controls (§11) --------------------------------------------

    def _nc_trial_quota_exceeded(self, source_ip, email):
        """True if this IP or email has created too many trials in the window."""
        get = self.env['ir.config_parameter'].sudo().get_param
        per_ip = int(get(_QUOTA_PER_IP_PARAM, 3))
        per_email = int(get(_QUOTA_PER_EMAIL_PARAM, 2))
        window_h = int(get(_QUOTA_WINDOW_PARAM, 24))
        since = fields.Datetime.subtract(fields.Datetime.now(), hours=window_h)
        Tenant = self.sudo()
        if source_ip and Tenant.search_count([
                ('checkout_source_ip', '=', source_ip), ('create_date', '>=', since)]) >= per_ip:
            return True
        if email and Tenant.search_count([
                ('email', '=ilike', email), ('create_date', '>=', since)]) >= per_email:
            return True
        return False

    def _nc_verify_recaptcha(self, token, remote_ip=None):
        """Verify a reCAPTCHA token. Feature-flagged: with no secret configured
        (dev / CI) verification is skipped and returns True. When a secret is
        set, a failed/errored verification returns False (fail closed)."""
        secret = self.env['ir.config_parameter'].sudo().get_param(_RECAPTCHA_SECRET_PARAM)
        if not secret:
            return True
        if not token:
            return False
        try:
            resp = requests.post(_RECAPTCHA_VERIFY_URL, data={
                'secret': secret, 'response': token, 'remoteip': remote_ip or '',
            }, timeout=10)
            return bool(resp.json().get('success'))
        except Exception:  # noqa: BLE001 - captcha outage must not silently pass
            _logger.warning("reCAPTCHA verification failed to reach Google", exc_info=True)
            return False

    # ---- verification -> provisioning ------------------------------------

    @staticmethod
    def _nc_new_checkout_token():
        return secrets.token_urlsafe(32)

    def _nc_send_verification_email(self):
        """Queue the branded verification email (best-effort — mail transport
        must never break the funnel; dev has no SMTP, so this queues a mail.mail
        rather than delivering). The template links to /nc/checkout/verify/<token>."""
        self.ensure_one()
        template = self.env.ref(
            'ncollection_saas.mail_template_checkout_verify', raise_if_not_found=False)
        if not template or not self.email:
            return
        try:
            template.send_mail(self.id, force_send=False)
        except Exception:  # noqa: BLE001 - transport must never break checkout
            _logger.warning("Verification email could not be queued for tenant %s",
                            self.id, exc_info=True)

    def _nc_confirm_checkout(self, token):
        """Consume a verification token: mark the tenant verified and activate
        its draft subscription, which triggers provisioning (P2-T02). Idempotent
        — a second click on an already-verified tenant is a no-op that still
        resolves. Returns the tenant (or empty recordset for a bad token)."""
        token = (token or '').strip()
        if not token:
            return self.browse()
        tenant = self.sudo().search([('checkout_token', '=', token)], limit=1)
        if not tenant:
            return self.browse()
        if not tenant.checkout_email_verified:
            tenant.checkout_email_verified = True
            sub = tenant.subscription_id or tenant.subscription_ids[:1]
            if sub and sub.status == 'draft':
                sub.action_activate()  # -> provisioning (SaaS override)
        return tenant
