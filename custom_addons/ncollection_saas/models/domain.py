# -*- coding: utf-8 -*-
"""Tenant domain & SSL tracking + automation (P2-T06).

Every provisioned tenant gets a `ncollection.domain` record. The common case is
a PLATFORM SUBDOMAIN (`<db>.ncollectionerp.com`): it rides the wildcard cert +
the single `server_name .ncollectionerp.com` nginx block from P1-T03, so a new
tenant is reachable over HTTPS with ZERO manual server work — this model just
*tracks* it and watches the cert's expiry.

CUSTOM domains (a tenant's own `erp.acme.com`) are scaffolded here (record type +
the nginx Jinja2 template + reload script) but their live issuance/reload is a
documented, deferred follow-up: they need per-domain DNS + their own Let's
Encrypt cert. Nothing on the subdomain acceptance path depends on that.

Lives in ncollection_saas: the platform automation layer (never queries a tenant
ERP model — Rule 3). Records are created by the provisioning engine and kept
healthy by a weekly reconciliation cron (same philosophy as config-sync P2-T03).
"""

import logging
from datetime import timedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# Days before SSL expiry at which a domain is flagged "expiring" and alerted.
SSL_ALERT_LEAD_DAYS = 14
# Re-alert cadence so the weekly cron never spams the same domain repeatedly.
SSL_REALERT_DAYS = 7

# Config parameters (non-secret; overridable per deployment).
_BASE_DOMAIN_PARAM = 'ncollection_saas.base_domain'
_DEFAULT_BASE_DOMAIN = 'ncollectionerp.com'
# The wildcard cert's current expiry (ISO date). A host-side job that can read
# the real cert file writes this (see docs/markdown/RUNBOOK_STAGING.md); until
# then subdomain records carry no expiry and read as "unknown".
_WILDCARD_EXPIRY_PARAM = 'ncollection_saas.wildcard_cert_expiry'


class NcollectionDomain(models.Model):
    _name = 'ncollection.domain'
    _description = 'Tenant Domain & SSL Certificate'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'ssl_expiry asc, id desc'
    _rec_name = 'fqdn'

    fqdn = fields.Char(
        string='Domain (FQDN)', required=True, index=True, tracking=True,
        help="Fully-qualified hostname, e.g. acme.ncollectionerp.com.")
    tenant_id = fields.Many2one(
        'ncollection.tenant', string='Tenant', required=True,
        ondelete='cascade', tracking=True)
    domain_type = fields.Selection(
        selection=[
            ('subdomain', 'Platform Subdomain'),
            ('custom', 'Custom Domain'),
        ],
        default='subdomain', required=True, tracking=True)
    # Subdomains ride the shared *.ncollectionerp.com wildcard cert (P1-T03);
    # custom domains get their own Let's Encrypt cert (deferred).
    is_wildcard = fields.Boolean(
        string='Rides Wildcard Cert', default=True, tracking=True)
    ssl_expiry = fields.Date(string='SSL Expiry', tracking=True)
    ssl_status = fields.Selection(
        selection=[
            ('valid', 'Valid'),
            ('expiring', 'Expiring Soon'),
            ('expired', 'Expired'),
            ('unknown', 'Unknown'),
        ],
        compute='_compute_ssl_status', string='SSL Status')
    last_alert_date = fields.Date(
        string='Last Expiry Alert', readonly=True,
        help="When the expiry alert last fired — throttles the weekly cron.")
    active = fields.Boolean(default=True)

    # Odoo 19 silently ignores `_sql_constraints` — use models.Constraint so
    # the unique index is actually created (constraint name ncollection_domain_fqdn_uniq).
    _fqdn_uniq = models.Constraint(
        'unique(fqdn)',
        'A domain record for this FQDN already exists.')

    # ---- computed status -------------------------------------------------

    @api.depends('ssl_expiry')
    def _compute_ssl_status(self):
        today = fields.Date.context_today(self)
        threshold = today + timedelta(days=SSL_ALERT_LEAD_DAYS)
        for domain in self:
            expiry = domain.ssl_expiry
            if not expiry:
                domain.ssl_status = 'unknown'
            elif expiry < today:
                domain.ssl_status = 'expired'
            elif expiry <= threshold:
                domain.ssl_status = 'expiring'
            else:
                domain.ssl_status = 'valid'

    # ---- helpers ---------------------------------------------------------

    @api.model
    def _base_domain(self):
        return (self.env['ir.config_parameter'].sudo().get_param(
            _BASE_DOMAIN_PARAM, _DEFAULT_BASE_DOMAIN) or '').strip().lower()

    @api.model
    def _wildcard_expiry(self):
        raw = self.env['ir.config_parameter'].sudo().get_param(
            _WILDCARD_EXPIRY_PARAM)
        if not raw:
            return False
        try:
            return fields.Date.to_date(raw)
        except (ValueError, TypeError):
            _logger.warning("Invalid %s config value: %r", _WILDCARD_EXPIRY_PARAM, raw)
            return False

    @api.model
    def _fqdn_for_tenant(self, tenant):
        """Subdomain FQDN for a tenant: <db>.<base-domain>. tenant key ===
        subdomain === database name (CLAUDE.md), so database_name is the label."""
        label = (tenant.database_name or '').strip().lower()
        return '%s.%s' % (label, self._base_domain()) if label else False

    # ---- provisioning hook + reconciliation ------------------------------

    @api.model
    def _sync_for_tenant(self, tenant):
        """Ensure the platform-subdomain domain record for a ready tenant
        exists and tracks the wildcard cert expiry. Idempotent: safe to call
        from provisioning success AND from the reconciliation cron."""
        fqdn = self._fqdn_for_tenant(tenant)
        if not fqdn:
            return self.browse()
        existing = self.with_context(active_test=False).search(
            [('tenant_id', '=', tenant.id), ('domain_type', '=', 'subdomain')],
            limit=1)
        vals = {
            'fqdn': fqdn,
            'is_wildcard': True,
            'ssl_expiry': self._wildcard_expiry(),
            'active': True,
        }
        if existing:
            existing.write(vals)
            return existing
        return self.create({'tenant_id': tenant.id, 'domain_type': 'subdomain', **vals})

    @api.model
    def _cron_scan_ssl_expiry(self):
        """Weekly reconciliation (P2-T06): backfill missing subdomain records
        for ready tenants, refresh expiry from the wildcard param, and alert on
        anything expiring within the lead window. Self-healing, idempotent."""
        tenants = self.env['ncollection.tenant'].search([
            ('database_status', '=', 'ready'), ('database_name', '!=', False)])
        for tenant in tenants:
            self._sync_for_tenant(tenant)
        for domain in self.search([]):
            domain._alert_if_expiring()
        return True

    def _alert_if_expiring(self):
        """Post a chatter warning + schedule a platform-admin activity when a
        cert is expiring/expired, throttled to once per SSL_REALERT_DAYS."""
        self.ensure_one()
        if self.ssl_status not in ('expiring', 'expired'):
            return
        today = fields.Date.context_today(self)
        if self.last_alert_date and (today - self.last_alert_date).days < SSL_REALERT_DAYS:
            return
        label = 'EXPIRED' if self.ssl_status == 'expired' else 'expiring soon'
        body = self.env._(
            "SSL certificate for %(fqdn)s is %(label)s (expiry: %(expiry)s).",
            fqdn=self.fqdn, label=label, expiry=self.ssl_expiry or 'unknown')
        self.message_post(body=body)
        self.activity_schedule(
            'mail.mail_activity_data_todo',
            summary=self.env._("Renew SSL: %s", self.fqdn),
            note=body)
        self.last_alert_date = today
