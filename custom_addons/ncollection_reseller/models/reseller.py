# -*- coding: utf-8 -*-
"""Reseller (white-label partner) — admin-DB platform model (P10-T09).

A reseller is a partner that provisions and manages sub-tenants under its own
brand. This record lives ONLY in the platform (admin) database (Rule 3): it is
never installed into, nor queried from, a tenant DB. Its brand defaults are
cascaded to each sub-tenant's res.company through the config-sync RPC bridge
(see models/tenant.py :: _config_sync_vals), not by any cross-DB access.
"""

import re

from odoo import api, fields, models
from odoo.exceptions import ValidationError

# Same strict 6-digit hex rule the tenant side enforces (ncollection_branding
# res.company._check_nc_brand_colors). Validating here too means a bad colour is
# rejected at the source, before it can ever be queued for a cascade push.
_HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_BRAND_COLOR_FIELDS = ("brand_primary_color", "brand_secondary_color",
                       "brand_sidebar_color")


class Reseller(models.Model):
    _name = 'ncollection.reseller'
    _description = 'NCollection White-Label Reseller'
    _inherit = ['mail.thread']
    _order = 'name asc'

    name = fields.Char(required=True, tracking=True)
    active = fields.Boolean(default=True)
    partner_id = fields.Many2one(
        'res.partner', string='Partner', required=True, tracking=True,
        help="The business partner (admin DB) this reseller account belongs to.")
    user_id = fields.Many2one(
        'res.users', string='Portal Login',
        help="The platform user who signs in to manage this reseller's "
             "sub-tenants. Record rules scope the reseller dashboard to the "
             "tenants owned by this user's reseller.")

    # ---- brand defaults cascaded to sub-tenants --------------------------
    brand_primary_color = fields.Char(
        string='Primary Colour',
        help="Reseller primary brand colour cascaded onto each sub-tenant "
             "(overrides the NCollection default). 6-digit hex, e.g. #1F5F8F.")
    brand_secondary_color = fields.Char(
        string='Secondary Colour', help="Reseller accent colour. 6-digit hex.")
    brand_sidebar_color = fields.Char(
        string='Sidebar Colour', help="Reseller sidebar colour. 6-digit hex.")
    brand_login_background = fields.Image(
        string='Login Background', max_width=1920, max_height=1080,
        help="Optional login-screen background cascaded to sub-tenants.")

    # ---- quota + revenue share -------------------------------------------
    max_subtenants = fields.Integer(
        string='Max Sub-Tenants', default=0,
        help="Maximum number of sub-tenants this reseller may provision. "
             "0 = unlimited (fail-open, like plan seat limits).")
    revenue_share_pct = fields.Float(
        string='Revenue Share (%)', default=0.0,
        help="Percentage of sub-tenant MRR owed to this reseller, used by the "
             "revenue-share report.")

    tenant_ids = fields.One2many(
        'ncollection.tenant', 'reseller_id', string='Sub-Tenants')
    subtenant_count = fields.Integer(
        string='Sub-Tenants', compute='_compute_subtenant_count')

    @api.depends('tenant_ids')
    def _compute_subtenant_count(self):
        # Grouped count so the form/list stays cheap with many resellers.
        counts = dict(self.env['ncollection.tenant']._read_group(
            [('reseller_id', 'in', self.ids)],
            groupby=['reseller_id'], aggregates=['__count']))
        for reseller in self:
            reseller.subtenant_count = counts.get(reseller, 0)

    @api.constrains(*_BRAND_COLOR_FIELDS)
    def _check_brand_colors(self):
        for reseller in self:
            for fname in _BRAND_COLOR_FIELDS:
                value = reseller[fname]
                if value and not _HEX_COLOR_RE.match(value):
                    raise ValidationError(self.env._(
                        "%(value)r is not a valid colour for '%(field)s'. Use a "
                        "6-digit hex colour such as #1F5F8F.",
                        value=value, field=self._fields[fname].string))

    @api.constrains('revenue_share_pct')
    def _check_revenue_share_pct(self):
        for reseller in self:
            if not (0.0 <= reseller.revenue_share_pct <= 100.0):
                raise ValidationError(self.env._(
                    "Revenue share must be between 0 and 100 percent."))

    @api.constrains('max_subtenants')
    def _check_max_subtenants(self):
        for reseller in self:
            if reseller.max_subtenants < 0:
                raise ValidationError(self.env._(
                    "Max sub-tenants cannot be negative (use 0 for unlimited)."))

    # ---- branding payload for the config-sync cascade --------------------
    def _nc_brand_sync_vals(self):
        """The brand keys to cascade to a sub-tenant's res.company, matching the
        _BRAND_APPLY_FIELDS contract on the tenant side (ncollection_core
        workspace.config). Only non-empty values are pushed, so an unset reseller
        colour leaves the NCollection default in place."""
        self.ensure_one()
        vals = {}
        if self.brand_primary_color:
            vals['brand_primary_color'] = self.brand_primary_color
        if self.brand_secondary_color:
            vals['brand_secondary_color'] = self.brand_secondary_color
        if self.brand_sidebar_color:
            vals['brand_sidebar_color'] = self.brand_sidebar_color
        if self.brand_login_background:
            # Image fields store base64 bytes; json2 needs a str.
            bg = self.brand_login_background
            vals['brand_login_background'] = bg.decode() if isinstance(bg, bytes) else bg
        return vals

    # ---- provision a sub-tenant under this reseller ----------------------
    def provision_subtenant(self, company_name, subdomain, plan,
                            email=None, contact_name=None, billing_cycle='monthly'):
        """Create a sub-tenant under this reseller and start provisioning.

        Reuses the platform's own tenant + subscription machinery (the same
        path public checkout uses): the ORM create enforces the reseller quota
        (models/tenant.py), subscription activation triggers the provisioning
        engine, and the reseller's brand cascades via config-sync — so the
        sub-tenant comes up carrying partner branding end-to-end. Returns the
        created tenant."""
        self.ensure_one()
        # Provisioning is a privileged platform operation: a reseller user has
        # only read on tenants (record-rule scoped), so the create runs sudo.
        # The quota guard in tenant.create still fires (it counts by reseller
        # regardless of su), and self is already the caller's own reseller
        # (reseller record rule), so this cannot provision under someone else.
        Tenant = self.env['ncollection.tenant'].sudo()
        Plan = self.env['ncollection.subscription.plan'].sudo()
        plan_rec = plan if getattr(plan, '_name', None) == 'ncollection.subscription.plan' \
            else Plan.search([('code', '=', plan), ('active', '=', True)], limit=1)
        if not plan_rec:
            raise ValidationError(self.env._("Unknown or inactive plan."))
        subdomain = Tenant._nc_normalize_subdomain(subdomain) \
            if hasattr(Tenant, '_nc_normalize_subdomain') else subdomain
        today = fields.Date.context_today(self.env.user)
        trial_days = plan_rec.trial_days or 14
        trial_end = fields.Date.add(today, days=trial_days)
        tenant = Tenant.create({
            'company_name': company_name,
            'contact_name': contact_name,
            'email': email,
            'domain': subdomain,
            'database_name': subdomain,
            'plan_id': plan_rec.id,
            'reseller_id': self.id,
            'status': 'trial',
            'database_status': 'not_provisioned',
            'onboarding_stage': 'signup',
            'trial_end_date': trial_end,
        })
        sub = self.env['ncollection.subscription'].sudo().create({
            'tenant_id': tenant.id,
            'plan_id': plan_rec.id,
            'billing_cycle': 'yearly' if billing_cycle == 'yearly' else 'monthly',
            'status': 'draft',
            'start_date': today,
            'end_date': trial_end,
        })
        tenant.subscription_id = sub.id
        # Activation triggers the SaaS provisioning override (P2-T02).
        sub.action_activate()
        return tenant

    def action_view_subtenants(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Sub-Tenants'),
            'res_model': 'ncollection.tenant',
            'view_mode': 'list,form',
            'domain': [('reseller_id', '=', self.id)],
            'context': {'default_reseller_id': self.id},
        }
