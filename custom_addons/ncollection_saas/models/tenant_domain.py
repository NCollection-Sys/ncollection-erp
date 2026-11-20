# -*- coding: utf-8 -*-
"""The tenant's addresses, as the Management tenant form shows them (Domains tab).

Option A - Platform Subdomain: the tenant answers on <label>.<base domain> (always, it is also the fallback).
Option B - Custom Domain: a verified external hostname becomes the primary address; the subdomain keeps answering and,
with the 'redirect' policy, sends browsers to the primary one.

Switching is a call to the tenant's own ``ncollection.admin.service.set_canonical_host`` over the tenant-administration
channel (same derived bearer, same allow-list as Users & Access): the workspace sets its web.base.url and redirect rule
itself. The database is never re-created, renamed or re-provisioned for an address change.
"""
from odoo import api, fields, models
from odoo.exceptions import UserError


class NcollectionTenant(models.Model):
    _inherit = 'ncollection.tenant'

    domain_ids = fields.One2many('ncollection.domain', 'tenant_id')
    primary_address = fields.Char(compute='_compute_primary_address')
    address_mode = fields.Selection(
        [('subdomain', 'Platform Subdomain'), ('custom', 'Custom Domain')],
        compute='_compute_primary_address', string='Address')

    @api.depends('domain_ids.is_primary', 'domain_ids.fqdn', 'domain_ids.domain_type', 'subdomain', 'database_name')
    def _compute_primary_address(self):
        for tenant in self:
            primary = tenant.domain_ids.filtered('is_primary')[:1]
            tenant.primary_address = primary.fqdn if primary else tenant._nc_subdomain_fqdn()
            tenant.address_mode = 'custom' if primary.domain_type == 'custom' else 'subdomain'

    def _nc_subdomain_fqdn(self):
        self.ensure_one()
        return self.env['ncollection.domain']._fqdn_for_tenant(self) or False

    def _nc_push_canonical_host(self, domain):
        """Tell the workspace its canonical host (``domain`` False = back to the platform subdomain)."""
        self.ensure_one()
        subdomain = self._nc_subdomain_fqdn()
        host = domain.fqdn if domain else subdomain
        if not host:
            raise UserError(self.env._("%s has no address yet.", self.company_name))
        aliases = {subdomain} | set(self.domain_ids.filtered(
            lambda d: d.domain_type == 'custom' and d.state == 'active').mapped('fqdn')) | {host}
        redirect = bool(domain and domain.domain_type == 'custom' and domain.redirect_policy == 'redirect')
        self._nc_admin_call('set_canonical_host', {
            'host': host, 'redirect': redirect, 'aliases': sorted(a for a in aliases if a)})
        return True

    def action_add_custom_domain(self):
        self.ensure_one()
        self.env['ncollection.domain'].check_access('create')
        return {
            'type': 'ir.actions.act_window', 'res_model': 'ncollection.domain', 'view_mode': 'form',
            'target': 'new', 'name': self.env._("Add a custom domain"),
            'context': {'default_tenant_id': self.id, 'default_domain_type': 'custom'},
        }
