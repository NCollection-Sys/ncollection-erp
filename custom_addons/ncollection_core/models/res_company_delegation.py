# -*- coding: utf-8 -*-
"""Company administration for a Workspace Administrator (Option A).

The tenant Owner holds ``group_tenant_admin`` but not ``base.group_erp_manager``, and Odoo lets only the latter write
``res.company``. The Workspace Administration Center's Company, Appearance and Accounting Defaults screens are plain
``res.company`` forms, so without this the Owner could no longer save their own company.

This is the one narrow delegation. A write by a Workspace Administrator runs with elevated rights ONLY when
  * every field written is in the allowlist (``_nc_tenant_admin_company_fields``, extended by the modules that own
    company-level business settings) - never currency, parent company, System Branding or a technical field,
  * every company written is one the user belongs to, and
  * every many2one value written belongs to that company (or a parent of it), so an account, tax or journal of
    another company cannot be attached.
Anything else is not delegated and falls through to Odoo's own access check, which refuses it. A user who already
holds base.group_erp_manager (the platform administrator) takes the native path unchanged. Each delegated write is
audited.
"""
from odoo import api, models
from odoo.exceptions import AccessError, ValidationError

TENANT_ADMIN = 'ncollection_core.group_tenant_admin'


class ResCompany(models.Model):
    _inherit = 'res.company'

    @api.model
    def _nc_tenant_admin_company_fields(self):
        """Company fields a Workspace Administrator may change. Modules add their company-level settings."""
        return {
            # identity (Workspace Administration > Company)
            'name', 'street', 'street2', 'city', 'state_id', 'zip', 'country_id', 'phone', 'email', 'website',
            'vat', 'company_registry', 'logo',
            # the tenant's own look (Workspace Administration > Appearance); System Branding is never here
            'nc_primary_color', 'nc_secondary_color', 'nc_sidebar_color', 'nc_login_background', 'nc_stamp',
        }

    def _nc_delegated_company_write(self, vals):
        """True when this write is a Workspace Administrator's change to their own company (see module docstring)."""
        if self.env.su or not vals:
            return False
        user = self.env.user
        if user.has_group('base.group_erp_manager') or not user.has_group(TENANT_ADMIN):
            return False
        if set(vals) - (self._nc_tenant_admin_company_fields() & set(self._fields)):
            return False
        if self - user.company_ids:
            raise AccessError(self.env._("You can only change the companies you belong to."))
        return True

    def _nc_check_delegated_values(self, vals):
        """Many2one values must belong to the company being changed (or a parent company)."""
        for name, value in vals.items():
            field = self._fields[name]
            if field.type != 'many2one' or not value:
                continue
            target = self.env[field.comodel_name].sudo().browse(value).exists()
            if not target:
                raise ValidationError(self.env._("The selected %(field)s does not exist.", field=field.string))
            # a record of one company (taxes, journals) or of several (accounts are shared through company_ids)
            owners = self.env['res.company']
            for owner_field in ('company_id', 'company_ids'):
                if owner_field in target._fields and target._fields[owner_field].comodel_name == 'res.company':
                    owners |= target[owner_field]
            for company in self:
                if owners and not (owners & company.parent_ids):
                    raise ValidationError(self.env._(
                        "%(field)s must belong to %(company)s.", field=field.string, company=company.name))

    def _nc_audit_delegated_write(self, vals):
        self.env.user._nc_audit('admin_settings_change', 'company %s: %s' % (
            ', '.join(str(i) for i in self.ids), ', '.join(sorted(vals))))

    def write(self, vals):
        # A module whose own res.company.write must run elevated too (it sits ABOVE this one in the MRO, e.g. Odoo's
        # account, which sets ir.default as the caller) elevates first - see ncollection_account_core - and this one
        # then sees env.su and passes through: one check, one audit.
        if self._nc_delegated_company_write(vals):
            self._nc_check_delegated_values(vals)
            result = super(ResCompany, self.sudo()).write(vals)
            self._nc_audit_delegated_write(vals)
            return result
        return super().write(vals)
