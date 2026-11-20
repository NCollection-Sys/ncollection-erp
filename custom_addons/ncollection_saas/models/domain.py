# -*- coding: utf-8 -*-
"""Tenant domains: the platform subdomain (P2-T06) and VERIFIED custom domains.

Every provisioned tenant has a PLATFORM SUBDOMAIN record (``<label>.<base domain>``): it rides the wildcard certificate
and the single ``server_name .<base domain>`` edge block, so this model only tracks it.

A CUSTOM domain (a tenant's own ``erp.customer.com``) is an alternative address, never a re-provisioning:

    Pending Verification --TXT token proven--> DNS Configuration Required --A/AAAA/CNAME reach this edge-->
    SSL Pending --edge agent issued the certificate and served /web/health over HTTPS--> Active
    (any step can end in Failed; Disabled stops routing; nothing here ever touches tenant data)

Who does what:
  * this model (platform database) holds the desired state and every authorisation decision: only a Platform
    Administrator on the tenant's allow-list can add, verify, activate, switch or disable a domain (ORM-enforced, not
    view-enforced, Rule 4);
  * the host's edge agent (scripts/edge/nc_edge_agent.py) pulls the domains that passed verification over an
    HMAC-authenticated endpoint (controllers/edge.py), re-checks ownership itself, issues the certificate, writes the
    nginx block that PINS the tenant database (X-NC-Tenant-DB) and reports back;
  * the tenant learns its canonical address over the tenant-administration channel (ncollection.admin.service
    ``set_canonical_host``) - web.base.url, links in mail, the optional redirect - so a domain change never needs the
    database to be re-created.

Lives in ncollection_saas: the platform automation layer (never queries a tenant ERP model - Rule 3).
"""

import ipaddress
import logging
import re
import secrets
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from ..tools import dnsquery

_logger = logging.getLogger(__name__)

# Days before SSL expiry at which a domain is flagged "expiring" and alerted.
SSL_ALERT_LEAD_DAYS = 14
# Re-alert cadence so the weekly cron never spams the same domain repeatedly.
SSL_REALERT_DAYS = 7

# Config parameters (non-secret; overridable per deployment).
# The wildcard cert's current expiry (ISO date). A host-side job that can read
# the real cert file writes this (see docs/markdown/RUNBOOK_STAGING.md); until
# then subdomain records carry no expiry and read as "unknown".
_WILDCARD_EXPIRY_PARAM = 'ncollection_saas.wildcard_cert_expiry'
# Where a customer points a custom domain. Per platform, never hardcoded: SaaS Settings > Custom Domains.
EDGE_IPV4_PARAM = 'ncollection_saas.custom_domain_ipv4'
EDGE_IPV6_PARAM = 'ncollection_saas.custom_domain_ipv6'
EDGE_CNAME_PARAM = 'ncollection_saas.custom_domain_cname'
ENABLED_PARAM = 'ncollection_saas.custom_domains_enabled'

VERIFY_PREFIX = '_nc-verify'
TOKEN_PREFIX = 'nc-verify='
_PLATFORM_GROUP = 'ncollection_subscription.group_platform_admin'
_LABEL_RE = re.compile(r'^(?!-)[a-z0-9-]{1,63}(?<!-)$')
_RESERVED_SUFFIXES = ('.localhost', '.local', '.internal', '.invalid', '.example', '.arpa', '.onion')
# Fields only this model's own transitions (and the edge report) may write.
_INTERNAL_FIELDS = {'state', 'verification_token', 'is_primary', 'last_verified', 'error_detail', 'dns_status',
                    'activated_on', 'ssl_expiry', 'edge_reported', 'cert_issuer', 'fqdn', 'tenant_id', 'domain_type'}

STATES = [
    ('pending_verification', 'Pending Verification'),
    ('dns_required', 'DNS Configuration Required'),
    ('ssl_pending', 'SSL Pending'),
    ('active', 'Active'),
    ('failed', 'Failed'),
    ('disabled', 'Disabled'),
]


def _is_ip(value):
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def normalize_fqdn(value):
    """Lower-case ASCII (IDNA) hostname without a trailing dot, or ValueError. No IPs, wildcards, single labels."""
    host = (value or '').strip().lower().rstrip('.')
    if host.startswith(('http://', 'https://')) or '/' in host or ':' in host or '*' in host or ' ' in host:
        raise ValueError('a hostname only (no scheme, port, path or wildcard)')
    try:
        host = host.encode('idna').decode('ascii')
    except UnicodeError as exc:
        raise ValueError('not a valid hostname') from exc
    if _is_ip(host):
        raise ValueError('an IP address is not a domain')
    labels = host.split('.')
    if len(labels) < 2 or len(host) > 253 or not all(_LABEL_RE.fullmatch(label) for label in labels):
        raise ValueError('not a valid hostname')
    if labels[-1].isdigit() or host == 'localhost' or host.endswith(_RESERVED_SUFFIXES):
        raise ValueError('a reserved or local name')
    return host


class NcollectionDomain(models.Model):
    _name = 'ncollection.domain'
    _description = 'Tenant Domain & SSL Certificate'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'is_primary desc, domain_type desc, ssl_expiry asc, id desc'
    _rec_name = 'fqdn'

    fqdn = fields.Char(
        string='Domain (FQDN)', required=True, index=True, tracking=True,
        help="Fully-qualified hostname, e.g. erp.customer.com.")
    tenant_id = fields.Many2one(
        'ncollection.tenant', string='Tenant', required=True,
        ondelete='cascade', tracking=True)
    domain_type = fields.Selection(
        selection=[
            ('subdomain', 'Platform Subdomain'),
            ('custom', 'Custom Domain'),
        ],
        default='subdomain', required=True, tracking=True)
    platform_domain = fields.Char(
        string='Platform', compute='_compute_platform_domain',
        help="The platform this record belongs to: its base domain.")
    state = fields.Selection(
        STATES, string='Status', default='active', required=True, tracking=True, index=True)
    is_primary = fields.Boolean(
        string='Primary Address', tracking=True,
        help="The tenant's canonical address: links, password resets, invitations and notifications use it.")
    redirect_policy = fields.Selection(
        [('alias', 'Serve on every address'),
         ('redirect', 'Redirect the other addresses to the primary')],
        default='alias', required=True, tracking=True,
        help="Applies when this domain is primary. The platform subdomain always keeps answering (fallback); with "
             "'redirect' a browser that opens it is sent to the primary address.")
    # The proof of control: a TXT record _nc-verify.<fqdn> = nc-verify=<token>. Random, per domain, cleared once the
    # domain is active (it is only needed until the edge has re-checked it).
    verification_token = fields.Char(copy=False, readonly=True, groups='base.group_system')
    verification_record = fields.Char(string='TXT record name', compute='_compute_instructions')
    verification_value = fields.Char(string='TXT record value', compute='_compute_instructions')
    dns_instructions = fields.Html(compute='_compute_instructions', sanitize=True)
    dns_status = fields.Selection(
        [('unknown', 'Not checked'), ('missing', 'Record missing'), ('wrong', 'Points elsewhere'),
         ('ok', 'Correct'), ('error', 'Lookup failed')],
        default='unknown', readonly=True)
    last_verified = fields.Datetime(readonly=True)
    error_detail = fields.Text(readonly=True)
    activated_on = fields.Datetime(readonly=True)
    edge_reported = fields.Datetime(string='Last Edge Report', readonly=True)
    cert_issuer = fields.Char(readonly=True)
    # Subdomains ride the shared *.<base domain> wildcard cert (P1-T03); custom domains get their own certificate.
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

    # Odoo 19 silently ignores `_sql_constraints` — use models.Constraint so the unique index is actually created
    # (ncollection_domain_fqdn_uniq). It covers ARCHIVED rows too: a hostname belongs to one tenant, ever, on this
    # platform; another platform on the same host is refused by the edge agent's claim register.
    _fqdn_uniq = models.Constraint(
        'unique(fqdn)',
        'A domain record for this FQDN already exists.')

    # ---- computed --------------------------------------------------------

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

    def _compute_platform_domain(self):
        base = self._base_domain()
        for domain in self:
            domain.platform_domain = base

    @api.depends('fqdn', 'domain_type', 'state')
    def _compute_instructions(self):
        targets = self._edge_targets()
        for domain in self:
            if domain.domain_type != 'custom' or not domain.fqdn:
                domain.verification_record = domain.verification_value = False
                domain.dns_instructions = False
                continue
            token = domain.sudo().verification_token
            domain.verification_record = '%s.%s' % (VERIFY_PREFIX, domain.fqdn)
            domain.verification_value = (TOKEN_PREFIX + token) if token else False
            domain.dns_instructions = domain._render_instructions(targets, token)

    def _render_instructions(self, targets, token):
        apex = self.fqdn.count('.') == 1
        rows = []
        if token:
            rows.append(('TXT', '%s.%s' % (VERIFY_PREFIX, self.fqdn), TOKEN_PREFIX + token,
                         self.env._("Proves you control the domain.")))
        if targets['cname'] and not apex:
            rows.append(('CNAME', self.fqdn, targets['cname'],
                         self.env._("Recommended for a subdomain such as erp.example.com.")))
        for kind, value in (('A', targets['ipv4']), ('AAAA', targets['ipv6'])):
            if not value:
                continue
            if apex and targets['cname']:
                note = self.env._("Required at the apex (a CNAME is not valid there; ALIAS/ANAME to %(target)s also "
                                  "works where your DNS provider offers it).", target=targets['cname'])
            else:
                note = self.env._("Use instead of the CNAME if your provider prefers address records.")
            rows.append((kind, self.fqdn, value, note))
        body = ''.join(
            '<tr><td><code>%s</code></td><td><code>%s</code></td><td><code>%s</code></td><td>%s</td></tr>'
            % tuple(self._html_escape(x) for x in row) for row in rows)
        heads = [self.env._("Type"), self.env._("Name"), self.env._("Value"), self.env._("Note")]
        intro = self.env._("Create these records at the DNS provider of %(fqdn)s, then press Verify.", fqdn=self.fqdn)
        outro = self.env._("The certificate is issued automatically once the records are visible. Remove any AAAA "
                           "record that does not point here, or validation fails.")
        return ('<p>%s</p><table class="table table-sm"><thead><tr><th>%s</th><th>%s</th><th>%s</th><th>%s</th></tr>'
                '</thead><tbody>%s</tbody></table><p class="text-muted">%s</p>') % (
            self._html_escape(intro), *heads, body, self._html_escape(outro))

    @staticmethod
    def _html_escape(value):
        return (str(value or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                .replace('"', '&quot;'))

    # ---- helpers ---------------------------------------------------------

    @api.model
    def _base_domain(self):
        # ONE definition (#476). It moved to ncollection.tenant so the tenant
        # model — which this module depends on, not the reverse — can build a
        # tenant URL from it too. A second copy here is how a platform ends up
        # with the domain configured in one place and read from another.
        return self.env['ncollection.tenant']._nc_base_domain()

    @api.model
    def _edge_targets(self):
        icp = self.env['ir.config_parameter'].sudo()
        return {'ipv4': (icp.get_param(EDGE_IPV4_PARAM) or '').strip(),
                'ipv6': (icp.get_param(EDGE_IPV6_PARAM) or '').strip(),
                'cname': (icp.get_param(EDGE_CNAME_PARAM) or '').strip().lower().rstrip('.')}

    @api.model
    def _custom_domains_enabled(self):
        return self.env['ir.config_parameter'].sudo().get_param(ENABLED_PARAM) in ('1', 'True', 'true')

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
        """Subdomain FQDN for a tenant: <label>.<base-domain>.

        #476: the label is now `tenant.subdomain`, which FALLS BACK to
        database_name — so every tenant provisioned before that field existed
        keeps exactly the FQDN it already had, and the records this model
        maintains need no migration.
        """
        label = tenant._nc_subdomain_label()
        return '%s.%s' % (label, self._base_domain()) if label else False

    def _internal(self):
        return self.with_context(nc_domain_internal=True)

    def _check_may_manage(self):
        """Platform Administrator on the tenant's allow-list, for every record: the same gate as Users & Access."""
        user = self.env.user
        if self.env.su:
            return
        if not user.has_group(_PLATFORM_GROUP):
            raise AccessError(self.env._("Only a Platform Administrator can manage tenant domains."))
        for tenant in self.mapped('tenant_id'):
            if user not in tenant.platform_admin_ids:
                raise AccessError(self.env._("You are not authorised to manage the domains of %s.",
                                             tenant.company_name or tenant.display_name))

    # ---- ORM guards ------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        internal = self.env.context.get('nc_domain_internal')
        for vals in vals_list:
            if vals.get('domain_type') == 'custom':
                if not internal and not self._custom_domains_enabled():
                    raise UserError(self.env._("Custom domains are not enabled on this platform (SaaS Settings)."))
                try:
                    vals['fqdn'] = normalize_fqdn(vals.get('fqdn'))
                except ValueError as exc:
                    raise ValidationError(self.env._("Invalid domain: %s.", exc)) from exc
                vals.update(state='pending_verification', is_wildcard=False, is_primary=False,
                            verification_token=secrets.token_urlsafe(32), dns_status='unknown')
            elif not internal and not self.env.su:
                raise AccessError(self.env._("Platform subdomains are created by provisioning, not by hand."))
        records = super().create(vals_list)
        customs = records.filtered(lambda d: d.domain_type == 'custom')
        customs._check_may_manage()
        customs._check_not_platform_namespace()
        return records

    def write(self, vals):
        if not self.env.context.get("nc_domain_internal") and not self.env.su:
            locked = _INTERNAL_FIELDS & set(vals)
            if locked:
                raise AccessError(self.env._("These fields change only through the domain actions: %s.",
                                             ', '.join(sorted(locked))))
            self._check_may_manage()
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_only_disabled_custom_domains(self):
        """Removing a domain record never touches the tenant, its database or its data. Provisioning and tenant
        deletion (superuser) may remove any record; a user may remove only a DISABLED custom domain."""
        if self.env.context.get("nc_domain_internal") or self.env.su:
            return
        self._check_may_manage()
        if any(d.domain_type == 'subdomain' for d in self):
            raise UserError(self.env._(
                "The platform subdomain is the tenant's fallback address and cannot be removed."))
        if any(d.state != 'disabled' for d in self):
            raise UserError(self.env._("Disable a custom domain before removing it."))

    def _check_not_platform_namespace(self):
        base = self._base_domain()
        for domain in self:
            if base and (domain.fqdn == base or domain.fqdn.endswith('.' + base)):
                raise ValidationError(self.env._(
                    "%s is inside the platform's own namespace; tenants get those addresses automatically.",
                    domain.fqdn))

    # ---- custom-domain lifecycle (UI actions) ----------------------------

    def action_verify(self):
        """Check the TXT proof and that the address reaches this edge. Moves to SSL Pending when both hold."""
        self._check_may_manage()
        targets = self._edge_targets()
        for domain in self.filtered(lambda d: d.domain_type == 'custom'):
            if domain.state not in ('pending_verification', 'dns_required', 'failed'):
                continue
            domain._internal().sudo()._nc_verify_once(targets)
        return True

    def _nc_verify_once(self, targets, resolver=None):
        self.ensure_one()
        resolver = resolver or dnsquery
        now = fields.Datetime.now()
        token = self.verification_token
        if not token:
            self.write({'verification_token': secrets.token_urlsafe(32)})
            token = self.verification_token
        try:
            proofs = resolver.txt_values('%s.%s' % (VERIFY_PREFIX, self.fqdn))
            v4, v6, cname = resolver.addresses(self.fqdn)
        except dnsquery.DnsError as exc:
            self.write({'dns_status': 'error', 'last_verified': now,
                        'error_detail': self.env._("DNS lookup failed (%s). Try again in a few minutes.", exc)})
            return False
        if TOKEN_PREFIX + token not in proofs:
            self.write({'state': 'pending_verification', 'dns_status': 'missing', 'last_verified': now,
                        'error_detail': self.env._("The TXT record %(name)s does not contain the expected value yet.",
                                                   name='%s.%s' % (VERIFY_PREFIX, self.fqdn))})
            return False
        reaches = ((targets['ipv4'] and targets['ipv4'] in v4) or (targets['ipv6'] and targets['ipv6'] in v6)
                   or (targets['cname'] and cname == targets['cname']))
        stray = (v4 - {targets['ipv4']}) | (v6 - {targets['ipv6']}) if not cname else set()
        if not reaches or stray:
            self.write({'state': 'dns_required', 'dns_status': 'wrong', 'last_verified': now,
                        'error_detail': self.env._(
                            "Ownership is proven, but %(fqdn)s resolves to %(found)s instead of this platform.",
                            fqdn=self.fqdn, found=', '.join(sorted(v4 | v6 | ({cname} if cname else set()))) or '-')})
            return False
        self.write({'state': 'ssl_pending', 'dns_status': 'ok', 'last_verified': now, 'error_detail': False})
        self.message_post(body=self.env._("Ownership and DNS verified; the certificate is being requested."))
        return True

    def action_set_primary(self):
        """Make this address the tenant's canonical one (an ACTIVE custom domain, or the subdomain to switch back)."""
        self.ensure_one()
        self._check_may_manage()
        if self.domain_type == 'custom' and self.state != 'active':
            raise UserError(self.env._("Only an active domain can become the primary address."))
        tenant = self.tenant_id
        previous = tenant.domain_ids.filtered('is_primary')
        # The tenant is told FIRST: if it refuses or cannot be reached, nothing changes here.
        tenant._nc_push_canonical_host(self)
        (tenant.domain_ids - self)._internal().sudo().write({'is_primary': False})
        self._internal().sudo().write({'is_primary': True})
        self.message_post(body=self.env._("Primary address: %(new)s (was %(old)s).", new=self.fqdn,
                                          old=', '.join(previous.mapped('fqdn')) or tenant._nc_subdomain_fqdn()))
        return True

    def action_disable(self):
        """Stop routing this custom domain. If it was primary, the tenant falls back to its platform subdomain."""
        self._check_may_manage()
        for domain in self.filtered(lambda d: d.domain_type == 'custom' and d.state != 'disabled'):
            if domain.is_primary:
                subdomain = domain.tenant_id.domain_ids.filtered(lambda d: d.domain_type == 'subdomain')[:1]
                if subdomain:
                    subdomain.action_set_primary()
                else:
                    domain.tenant_id._nc_push_canonical_host(False)
                    domain._internal().sudo().write({'is_primary': False})
            domain._internal().sudo().write({'state': 'disabled', 'error_detail': False})
            domain.message_post(body=domain.env._("Disabled: the edge stops serving %s at its next run.", domain.fqdn))
        return True

    def action_enable(self):
        """Re-enable a disabled custom domain: ownership is proven again from scratch (a NEW token)."""
        self._check_may_manage()
        for domain in self.filtered(lambda d: d.domain_type == 'custom' and d.state == 'disabled'):
            domain._internal().sudo().write({'state': 'pending_verification', 'dns_status': 'unknown',
                                             'verification_token': secrets.token_urlsafe(32), 'error_detail': False})
        return True

    # ---- the edge agent's view (controllers/edge.py) ---------------------

    @api.model
    def _nc_edge_state(self):
        """What the edge must serve. Only domains past ownership verification; the token only while SSL is pending."""
        rows = []
        domains = self.sudo().search([('domain_type', '=', 'custom'), ('state', 'in', ('ssl_pending', 'active'))])
        for domain in domains:
            tenant = domain.tenant_id
            if tenant.database_status != 'ready' or not tenant.database_name:
                continue
            rows.append({
                'fqdn': domain.fqdn, 'database': tenant.database_name, 'state': domain.state,
                'token': domain.verification_token if domain.state == 'ssl_pending' else None,
                'primary': domain.is_primary, 'redirect': domain.redirect_policy,
                'subdomain': tenant._nc_subdomain_fqdn(),
            })
        return {'platform': self._base_domain(), 'enabled': self._custom_domains_enabled(), 'domains': rows}

    @api.model
    def _nc_edge_report(self, results):
        """Apply the edge agent's results. Unknown names are ignored; a result never creates or moves a domain."""
        applied = 0
        for item in results if isinstance(results, list) else []:
            if not isinstance(item, dict):
                continue
            try:
                fqdn = normalize_fqdn(item.get('fqdn'))
            except ValueError:
                continue
            domain = self.sudo().search([('fqdn', '=', fqdn), ('domain_type', '=', 'custom')], limit=1)
            if not domain or domain.state not in ('ssl_pending', 'active'):
                continue
            domain = domain._internal()
            expiry = False
            if item.get('expiry'):
                try:
                    expiry = fields.Date.to_date(str(item['expiry'])[:10])
                except (ValueError, TypeError):
                    expiry = False
            now = fields.Datetime.now()
            if item.get('ok') is True and expiry:
                vals = {'ssl_expiry': expiry, 'edge_reported': now, 'error_detail': False,
                        'cert_issuer': str(item.get('issuer') or '')[:120] or False}
                if domain.state == 'ssl_pending':
                    vals.update(state='active', activated_on=now, verification_token=False, last_verified=now)
                domain.write(vals)
                if vals.get('state') == 'active':
                    domain.message_post(body=domain.env._("%s is active over HTTPS.", domain.fqdn))
            else:
                detail = str(item.get('error') or 'the edge could not activate this domain')[:500]
                vals = {'edge_reported': now, 'error_detail': detail}
                if domain.state == 'ssl_pending' and item.get('final'):
                    vals['state'] = 'failed'
                domain.write(vals)
            applied += 1
        return applied

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
            'state': 'active',
        }
        if existing:
            existing._internal().write(vals)
            return existing
        is_primary = not tenant.domain_ids.filtered('is_primary')
        return self._internal().create({'tenant_id': tenant.id, 'domain_type': 'subdomain',
                                        'is_primary': is_primary, **vals})

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
        self._internal().write({'last_alert_date': today})
