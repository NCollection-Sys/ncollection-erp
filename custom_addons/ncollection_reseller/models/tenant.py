# -*- coding: utf-8 -*-
"""Sub-tenant link + brand cascade + provisioning quota (P10-T09).

Extends ncollection.tenant (admin DB) with a reseller parent link, enforces the
reseller's provisioning quota at the ORM create layer (path-agnostic — a UI
action, a controller, or a raw create all hit it, Rule 4/7), and augments the
config-sync payload with the reseller's brand so each sub-tenant carries partner
branding end-to-end. No tenant-DB access happens here: the brand keys ride the
existing per-tenant-keyed config-sync RPC channel (ncollection_saas).
"""

from odoo import api, fields, models
from odoo.exceptions import ValidationError


class Tenant(models.Model):
    _inherit = 'ncollection.tenant'

    reseller_id = fields.Many2one(
        'ncollection.reseller', ondelete='restrict',
        tracking=True, index=True,
        help="The white-label reseller that provisioned this sub-tenant. "
             "Empty for direct (non-reseller) tenants — the standard checkout "
             "flow is unchanged.")

    # ---- provisioning quota (ORM-level, path-agnostic) -------------------
    @api.model
    def _nc_check_reseller_quota(self, reseller, adding=1):
        """Raise if provisioning `adding` more sub-tenants would exceed the
        reseller's max_subtenants. 0 = unlimited (fail-open)."""
        if not reseller or not reseller.max_subtenants:
            return
        current = self.search_count([('reseller_id', '=', reseller.id)])
        if current + adding > reseller.max_subtenants:
            raise ValidationError(self.env._(
                "Reseller '%(name)s' has reached its sub-tenant quota "
                "(%(max)s). Provisioning is blocked until the limit is raised.",
                name=reseller.name, max=reseller.max_subtenants))

    @api.model_create_multi
    def create(self, vals_list):
        # Enforce per-reseller, batching by reseller so a single multi-create
        # cannot slip past the limit.
        pending = {}
        for vals in vals_list:
            rid = vals.get('reseller_id')
            if rid:
                pending[rid] = pending.get(rid, 0) + 1
        for rid, count in pending.items():
            self._nc_check_reseller_quota(
                self.env['ncollection.reseller'].browse(rid), adding=count)
        return super().create(vals_list)

    # ---- brand cascade: augment the config-sync payload ------------------
    def _config_sync_vals(self):
        """Add the reseller's brand keys to the pushed config so the tenant's
        res.company picks up partner branding (applied override-if-default on the
        tenant side, ncollection_core workspace.config._nc_apply_pushed_branding).
        Non-reseller tenants get exactly today's payload — fully backward
        compatible."""
        vals = super()._config_sync_vals()
        if self.reseller_id:
            vals.update(self.reseller_id._nc_brand_sync_vals())
        return vals
