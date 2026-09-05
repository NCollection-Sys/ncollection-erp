# -*- coding: utf-8 -*-
"""Per-tenant module overrides (#476).

THE PROBLEM. Entitlement came from the plan alone, so the only way to take one
module away from one customer was to edit the plan — which changes it for every
tenant on that plan — or to move that customer onto a plan of their own. Both
are wrong for "Atlas does not want CRM".

THE SHAPE. An override is a DELTA against the plan, never a replacement for it:

    plan modules  UNION  localization package  MINUS  disabled  UNION  added

so the plan stays the source of truth for what the customer bought, and the
override records the deliberate exception. Removing the override restores the
plan's answer exactly, which is what makes it safe to undo.

WHY A MODEL AND NOT A TEXT FIELD. The plan's own module list is a comma-string
because it crosses the platform/tenant boundary as one value. An override does
not cross that boundary — it is resolved platform-side before the licensed list
is built — so it can be a real record, with a reason, an author and a date. That
matters: "who turned CRM off for this customer, and why" is the first question
asked when a tenant reports a missing app.

REVOCATION IS NOT UNINSTALLATION, and that rule is untouched here. Disabling a
module withdraws the licence and hides the menus (Ring 1/Ring 2); it never drops
the module or its tables, because dropping them would destroy customer data that
cannot be restored.
"""
from odoo import api, fields, models
from odoo.exceptions import ValidationError


class TenantModuleOverride(models.Model):
    _name = 'ncollection.tenant.module.override'
    _description = 'Per-Tenant Module Override'
    _order = 'tenant_id, module_name'
    _rec_name = 'module_name'

    tenant_id = fields.Many2one(
        'ncollection.tenant', required=True,
        ondelete='cascade', index=True)
    module_name = fields.Char(
        string='Technical Name', required=True,
        help="The module's technical name, e.g. 'crm'.")
    mode = fields.Selection(
        selection=[
            ('disable', 'Disabled for this tenant'),
            ('enable', 'Added for this tenant'),
        ],
        default='disable', required=True,
        help="Disabled removes a module the plan grants; Added grants one the "
             "plan does not.")
    reason = fields.Char(
        help="Why this tenant differs from its plan. Shown to whoever asks why "
             "a customer is missing an app.")
    active = fields.Boolean(default=True)

    # One decision per module per tenant. Two rows for the same module would
    # make the effective list depend on row order, which is not a thing anyone
    # can reason about.
    _tenant_module_uniq = models.Constraint(
        'unique(tenant_id, module_name)',
        'This tenant already has an override for that module.')

    @api.constrains('module_name')
    def _check_module_name(self):
        """The name must be a module that exists on this platform.

        A typo is otherwise invisible: a 'disable' for a module nobody has
        silently does nothing, and an 'enable' for one queues an install job
        that fails on the tenant's database long after the click.
        """
        Module = self.env['ir.module.module'].sudo()
        for override in self:
            name = (override.module_name or '').strip()
            if not name:
                continue
            if not Module.search_count([('name', '=', name)]):
                raise ValidationError(self.env._(
                    "'%s' is not a module on this platform.", name))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('module_name'):
                vals['module_name'] = vals['module_name'].strip()
        overrides = super().create(vals_list)
        overrides._nc_apply_to_tenant()
        return overrides

    def write(self, vals):
        res = super().write(vals)
        self._nc_apply_to_tenant()
        return res

    def unlink(self):
        # Captured BEFORE the rows go: after unlink there is nothing to read the
        # tenants from, and removing an override has to re-push licensing just
        # as adding one does — otherwise "remove override" restores the plan's
        # answer in the model and not in the tenant.
        tenants = self.mapped('tenant_id')
        res = super().unlink()
        tenants._nc_push_entitlement_change()
        return res

    def _nc_apply_to_tenant(self):
        """Push the new entitlement through the EXISTING lifecycle.

        No second install path and no second sync: this calls the same hook a
        plan edit calls, so an override reaches the tenant by exactly the route
        a plan change does — queued, deduplicated, and retryable.
        """
        self.mapped('tenant_id')._nc_push_entitlement_change()
