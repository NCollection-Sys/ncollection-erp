# -*- coding: utf-8 -*-
"""The platform's System Branding reaches every tenant (config-sync).

System Branding is the identity of the SYSTEM (its name, short name, browser title, colours, logos and favicon), and
it is the platform's to set (docs/markdown/SYSTEM_BRANDING.md). Until this, a tenant kept the defaults it was seeded
with: the platform showed its configured system name while every tenant still showed the seeded default - its
sign-in page, its Discuss
bot, its installable app. The identity now rides the config-sync push like the ECB rate (#308) does:

* the payload carries the platform's own record (read from the platform's own model: Rule 3);
* it is sent only to a tenant that advertised ``accepts_system_branding`` in a previous response, so a tenant running
  older code never receives a key its whitelist would refuse (which would take licensing sync down with it);
* the TENANT applies it through ncollection_branding's own whitelisted, validated write;
* a change to the platform's System Branding re-syncs every ready tenant through the same queue.

Never the tenant's colours, and never over the company's own identity: those are separate (three identities, three
places). The same push carries the company's CONTACT details from the Tenants record (email, phone, the tenant's own
address as its website), which a tenant only ever had as shipped placeholders (noreply@ncollection.com); the tenant
applies them only where a placeholder or nothing is stored.
"""
from odoo import api, fields, models


class TenantSystemBrandingSync(models.Model):
    _inherit = 'ncollection.tenant'

    config_sync_accepts_system_branding = fields.Boolean(
        string='Accepts Pushed System Branding', readonly=True, copy=False,
        help="Set from the tenant's own sync RESPONSE. The platform sends its System Branding only after a tenant has "
             "advertised it.")

    def _record_sync_capabilities(self, payload):
        result = super()._record_sync_capabilities(payload)
        accepts = bool(isinstance(payload, dict) and payload.get('accepts_system_branding'))
        if accepts != self.config_sync_accepts_system_branding:
            self.sudo().write({'config_sync_accepts_system_branding': accepts})
        return result

    def sync_workspace_config(self):
        """A tenant that advertises the capability for the first time on THIS push (a workspace just provisioned, or
        just upgraded) receives the system's identity in a second push now, not at the next reconciliation hours
        later: until then it showed the seeded default name to its first users. Only on that change: never a loop."""
        before = {tenant.id: tenant.config_sync_accepts_system_branding for tenant in self}
        result = super().sync_workspace_config()
        for tenant in self:
            if (not before[tenant.id] and tenant.config_sync_accepts_system_branding
                    and tenant.database_status == 'ready' and tenant.database_name):
                tenant._config_sync_push(tenant.database_name, tenant._config_sync_vals())
        return result

    def _config_sync_record(self, state, error=None):
        # Same self-healing rule as the rate: a failed push drops the key, so the next attempt is the payload that
        # is known to work.
        if state != 'ok':
            self.filtered('config_sync_accepts_system_branding').sudo().write(
                {'config_sync_accepts_system_branding': False})
        return super()._config_sync_record(state, error=error)

    def _config_sync_vals(self):
        vals = super()._config_sync_vals()
        if not self.config_sync_accepts_system_branding:
            return vals
        identity = self.env['ncollection.product.branding'].sudo()._nc_platform_identity()
        if identity:
            vals['nc_system_branding'] = identity
        # The company's contact details as entered in Tenants: only what exists (nothing is invented). The tenant
        # applies them only over the shipped placeholders or an empty field, never over what its Owner entered.
        # Every key is sent, empty when the Tenants record has no value: the tenant then clears a shipped placeholder
        # (noreply@ncollection.com) instead of keeping it as if it were the company's address. Nothing is invented.
        vals['nc_company_identity'] = {'email': self.email or '', 'phone': self.phone or '',
                                       'website': self.tenant_url or ''}
        return vals


class PlatformSystemBranding(models.Model):
    _inherit = 'ncollection.product.branding'

    def write(self, vals):
        result = super().write(vals)
        self._nc_resync_tenants()
        return result

    @api.model
    def _nc_resync_tenants(self):
        """Every ready tenant receives the changed identity through the one config-sync queue (deduplicated per
        tenant). On a tenant database there is no tenant registry, so nothing is queued there."""
        tenants = self.env['ncollection.tenant'].sudo().search([
            ('database_status', '=', 'ready'), ('database_name', '!=', False)])
        if tenants:
            tenants._config_sync_enqueue()
