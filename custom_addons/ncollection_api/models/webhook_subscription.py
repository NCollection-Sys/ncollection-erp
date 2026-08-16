import hashlib
import hmac
import ipaddress
import secrets
import urllib.parse

from odoo import api, fields, models
from odoo.exceptions import ValidationError


class NCollectionWebhookSubscription(models.Model):
    """Event subscription model for outgoing webhooks (P8-T03).

    Allows external clients / tenants to register HTTP endpoints that receive
    cryptographically signed events with at-least-once delivery semantics.
    """
    _name = 'ncollection.webhook.subscription'
    _description = 'NCollection Webhook Subscription'
    _order = 'create_date desc, id desc'

    name = fields.Char(required=True)
    client_id = fields.Many2one(
        'ncollection.api.client',
        string="API Client",
        ondelete='set null',
        help="Optional API client that owns this webhook subscription",
    )
    target_url = fields.Char(
        string="Target URL",
        required=True,
        help="HTTPS URL endpoint where signed event payloads will be delivered",
    )
    secret = fields.Char(
        string="Signing Secret",
        required=True,
        copy=False,
        default=lambda self: self._nc_generate_secret(),
        help="Secret key used to compute HMAC-SHA256 signatures",
    )
    active = fields.Boolean(default=True, index=True)
    event_types = fields.Char(
        string="Subscribed Events",
        required=True,
        default="*",
        help="Comma-separated event names (e.g. sale.order.confirmed,account.move.posted) or * for all",
    )
    state = fields.Selection(
        [
            ('active', 'Active'),
            ('degraded', 'Degraded'),
            ('disabled', 'Disabled'),
        ],
        default='active',
        required=True,
        help="State of the webhook subscription based on recent delivery health",
    )
    failure_count = fields.Integer(
        string="Consecutive Failures",
        default=0,
        readonly=True,
        help="Number of consecutive failed delivery attempts",
    )
    delivery_ids = fields.One2many(
        'ncollection.webhook.delivery',
        'subscription_id',
        string="Delivery Logs",
    )
    delivery_count = fields.Integer(
        compute='_compute_delivery_count',
    )

    @api.depends('delivery_ids')
    def _compute_delivery_count(self):
        for rec in self:
            rec.delivery_count = len(rec.delivery_ids)

    @api.model
    def _nc_generate_secret(self):
        """Generate a cryptographically secure signing secret."""
        return 'nc_whsec_' + secrets.token_hex(24)

    @api.constrains('target_url')
    def _check_target_url(self):
        for rec in self:
            if rec.target_url:
                rec._nc_validate_url(rec.target_url)

    def _nc_validate_url(self, url):
        """SSRF Guard: Validates that the webhook URL is valid and does not target

        dangerous internal addresses (e.g. loopback, link-local, private networks)
        unless in test mode.
        """
        if not url:
            raise ValidationError(self.env._("Target URL cannot be blank."))
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in ('http', 'https'):
            raise ValidationError(self.env._("Target URL scheme must be HTTP or HTTPS."))
        hostname = parsed.hostname
        if not hostname:
            raise ValidationError(self.env._("Target URL must have a valid hostname."))

        # In testing environments, allow localhost / 127.0.0.1
        if self.env.registry.in_test if hasattr(self.env.registry, 'in_test') else False:
            return

        try:
            ip = ipaddress.ip_address(hostname)
            if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved:
                raise ValidationError(self.env._("Target URL cannot point to internal or private IP addresses."))
        except ValueError:
            # Hostname is a domain name
            if hostname.lower() in ('localhost', '127.0.0.1', '::1', '0.0.0.0'):
                raise ValidationError(self.env._("Target URL cannot point to localhost."))

    def _nc_sign_payload(self, timestamp, payload_str):
        """Compute the HMAC-SHA256 signature for a webhook payload."""
        self.ensure_one()
        secret = self.secret or ''
        data = f"{timestamp}.{payload_str}".encode('utf-8')
        signature = hmac.new(secret.encode('utf-8'), data, hashlib.sha256).hexdigest()
        return f"sha256={signature}"

    def _nc_matches_event(self, event_name):
        """Check if this subscription is interested in the given event."""
        self.ensure_one()
        if not self.active or self.state == 'disabled':
            return False
        subscribed = [e.strip() for e in (self.event_types or '').split(',') if e.strip()]
        if '*' in subscribed:
            return True
        return event_name in subscribed
