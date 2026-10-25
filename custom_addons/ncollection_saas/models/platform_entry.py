# -*- coding: utf-8 -*-
"""Enter a tenant as its administrator from the Platform Summary ("Enter Company").

Database-per-tenant, so entering is not a company switch and it never replays a password. The platform
administrator's browser is sent to the tenant's own subdomain with a one-time token whose SHA-256 this layer has
just registered, over the existing per-tenant authenticated service channel, in the tenant's own database
(``ncollection.platform.entry`` in ncollection_core). The tenant redeems it once for a passwordless, named proxy
user. Rule 3 holds: this is the layer that may reach a tenant database, and it does so through RPC, never SQL.

What decides whether you may enter, ALL on the platform and ALL server-side:
  * you are a platform administrator (group), AND
  * you are on THIS tenant's ``platform_admin_ids`` allow-list, AND
  * the tenant is provisioned and not suspended/expired.
The target database is ``tenant.database_name`` from the registry record, never a value typed or posted by the
caller, so there is no way to name a database that is not a registered tenant.

Attribution: the entry is written to the tenant's chatter as the REAL platform user, and inside the tenant every
change is made by that person's proxy user ("Platform Admin: <name>"), never by an anonymous Admin.
"""
import hashlib
import json
import logging
import os
import re
import secrets
from urllib.parse import urlparse

import requests

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.ncollection_core.app_url import app_url  # server-wide: the one application URL builder

from .config_sync import _RPC_TIMEOUT, _SYNC_KEY_ENV, derive_tenant_key

_logger = logging.getLogger(__name__)

_GRANT_ENDPOINT = '/json/2/ncollection.platform.entry/grant_from_platform'
_ENTER_PATH = '/ncollection/platform/enter'
_PLATFORM_GROUP = 'ncollection_subscription.group_platform_admin'
_DB_RE = re.compile(r'^[a-z][a-z0-9]{1,62}$')            # a tenant key is also its subdomain: alphanumeric
_GRANT_TTL = 60
_MAX_REPLY = 64 * 1024


class Tenant(models.Model):
    _inherit = 'ncollection.tenant'

    def action_enter_company(self):
        """Open this tenant as its administrator. Returns a URL action to the tenant's own subdomain."""
        self.ensure_one()
        user = self.env.user
        if not user.has_group(_PLATFORM_GROUP):
            raise AccessError(self.env._("Only a Platform Administrator can enter a company."))
        if user not in self.platform_admin_ids:
            raise AccessError(self.env._("You are not authorised to enter %s.", self.company_name))
        if self.database_status != 'ready' or self.status not in ('trial', 'active'):
            raise UserError(self.env._(
                "%s cannot be entered right now (it is not provisioned, or it is suspended or expired).",
                self.company_name))
        db = (self.database_name or '').strip()
        if not _DB_RE.fullmatch(db):
            raise UserError(self.env._("%s has no valid database registered.", self.company_name))
        if not os.environ.get(_SYNC_KEY_ENV):
            raise UserError(self.env._("The platform is not configured to reach tenants (service key missing)."))
        # THE RELEASE CONTRACT, BEFORE ANY TOKEN EXISTS: a tenant whose database the code is ahead of (or that is held
        # for an upgrade) would answer the redeem with its maintenance page at best, and an HTTP 500 at worst before
        # the release gate. Ask its gate now and refuse cleanly: no token, no grant. A notification, not an exception:
        # an exception would roll back the reason just recorded on the tenant form, which is what the operator needs.
        state = self._nc_release_refresh()
        if state != 'ready':
            _logger.warning("Platform entry: user %s refused into tenant %s (release state %s)", user.login, db, state)
            return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
                'type': 'warning', 'sticky': True,
                'title': self.env._("Not ready for the current release"),
                'message': self.env._(
                    "%(company)s cannot be entered right now: its workspace is not ready for the current release "
                    "(%(state)s). No entry was issued. The Release section of the tenant shows why.",
                    company=self.company_name,
                    state=self._fields['nc_release_state'].convert_to_export(state, self)),
                'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
            }}

        token = secrets.token_urlsafe(32)
        grant = {
            'token_hash': hashlib.sha256(token.encode()).hexdigest(),     # the token itself never leaves this method
            'platform_ref': 'u%d' % user.id,
            'platform_name': user.name,
            'return_url': self._nc_platform_return_url(),
            'ttl': _GRANT_TTL,
        }
        self._nc_push_entry_grant(db, grant)
        self.sudo().message_post(
            body=self.env._("%s entered this company as Platform Administrator.", user.name),
            author_id=user.partner_id.id, subtype_xmlid='mail.mt_note')
        _logger.info("Platform entry: user %s (id %s) -> tenant %s", user.login, user.id, db)
        return {'type': 'ir.actions.act_url', 'url': self._nc_entry_url(token), 'target': 'self'}

    # ------------------------------------------------------------------ helpers
    def _nc_platform_return_url(self):
        """Where "Back to Platform" returns to: this platform's own web address."""
        url = (self.env['ir.config_parameter'].sudo().get_param('web.base.url') or '').rstrip('/')
        return url + app_url() if url else ''

    def _nc_entry_url(self, token):
        """https://<tenant host>/ncollection/platform/enter#token=... (scheme follows how the platform is served)."""
        self.ensure_one()
        # WHO RECEIVES THE TOKEN is decided by system-owned configuration ONLY. A tenant's own fields are writable by
        # every platform administrator (tenant_url is derived from a constrained subdomain + the system-only base
        # domain, but portal_url is a plain Char), so none of them may choose the host: an administrator who is not on
        # this tenant's allow-list could otherwise point it at their own server, wait for an authorised administrator
        # to click "Enter Company", read the one-time token from the fragment and redeem it here. The address is the
        # platform's one tenant-URL builder (<subdomain>.<base domain>, base domain from system configuration):
        # the same value the tenant list shows and the routing layer resolves. There is no second, entry-only template.
        address = urlparse(self._nc_public_origin() or '')
        if address.scheme not in ('http', 'https') or not address.netloc or '@' in address.netloc:
            raise UserError(self.env._("%s has no safe address to enter.", self.company_name))
        return '%s://%s%s#token=%s' % (address.scheme, address.netloc, _ENTER_PATH, token)   # fragment: never sent

    def _nc_push_entry_grant(self, db, grant):
        """Register one grant in the tenant database over json2 + the tenant's own derived bearer."""
        master = os.environ.get(_SYNC_KEY_ENV)
        if not master:
            raise UserError(self.env._("The platform is not configured to reach tenants (service key missing)."))
        try:
            resp = requests.post(
                self._config_sync_base_url() + _GRANT_ENDPOINT,
                headers={
                    'Host': self._nc_service_host(db),
                    'Authorization': 'Bearer %s' % derive_tenant_key(master, db),
                    'X-Odoo-Database': db, 'Content-Type': 'application/json', 'Accept-Encoding': 'identity',
                },
                data=json.dumps({'vals': grant}), timeout=_RPC_TIMEOUT, allow_redirects=False, stream=True)
        except requests.RequestException as exc:
            _logger.error("Platform entry grant to %s failed (transport): %s", db, exc)
            raise UserError(self.env._("%s could not be reached. Try again in a moment.", self.company_name))
        try:
            body = resp.raw.read(_MAX_REPLY + 1, decode_content=True) if resp.status_code == 200 else b''
            ok = resp.status_code == 200 and len(body) <= _MAX_REPLY and (json.loads(body or b'{}') or {}).get('ok')
        except (ValueError, OSError):
            ok = False
        finally:
            resp.close()
        if not ok:
            _logger.error("Platform entry grant to %s refused (HTTP %s)", db, resp.status_code)
            raise UserError(self.env._(
                "%(company)s did not accept the entry (HTTP %(status)s). Its workspace is probably behind this "
                "platform's code. Upgrade every NCollection module named by the upgrade preflight for it, together "
                "in one run (never ncollection_core alone), then try again.",
                company=self.company_name, status=resp.status_code))

    @api.model
    def _nc_enterable_for(self, user=None):
        """The tenants ``user`` may enter right now: no tenant database is touched."""
        return self._nc_my_companies_for(user).filtered(lambda t: t.database_status == 'ready')

    @api.model
    def _nc_my_companies_for(self, user=None):
        """The companies ``user`` is authorised for, whatever stage their workspace is at.

        A company that is not built yet (awaiting approval, queued, building, or whose build failed) is LISTED, with
        its stage, rather than hidden:
        "I created it and it is not there" is the worst answer a list can give. Only a ready one can be entered
        (``action_enter_company`` re-checks that on the server); the rest show their stage instead of a button.
        """
        user = user or self.env.user
        if not user.has_group(_PLATFORM_GROUP):
            return self.browse()
        return self.search([('platform_admin_ids', 'in', user.id), ('status', 'in', ('trial', 'active'))])


class PlatformSummary(models.TransientModel):
    """The platform Summary lists "My Companies": a plain read of the registry, never a scan of tenant databases."""
    _inherit = 'ncollection.subscription.dashboard'

    enterable_tenant_ids = fields.Many2many(
        'ncollection.tenant', compute='_compute_enterable_tenant_ids', string='My Companies')

    def _compute_enterable_tenant_ids(self):
        tenants = self.env['ncollection.tenant']._nc_my_companies_for()
        for summary in self:
            summary.enterable_tenant_ids = tenants
