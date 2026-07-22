# -*- coding: utf-8 -*-
"""Per-tenant workspace configuration (P1-T09).

One record per tenant database, written by the provisioning pipeline
(P2-T01) and kept in sync on plan changes (P2-T03). The tenant itself
never edits it (ACL: write restricted to base.group_system).

This model feeds Ring 1 of license defense-in-depth (menu visibility,
see models/ir_ui_menu.py). Ring 2 (ORM enforcement, P1-T10) will consume
the same record.
"""

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

# The only fields the platform config-sync channel (P2-T03) may push. Any other
# key in an inbound sync payload is rejected — the RPC service account can write
# nothing else on this model, and this method is its single entry point.
_SYNCABLE_FIELDS = frozenset({
    'allowed_module_names', 'plan_code', 'subscription_status', 'max_users',
})


class WorkspaceConfig(models.Model):
    _name = 'ncollection.workspace.config'
    _description = 'NCollection Workspace Configuration'

    allowed_module_names = fields.Text(
        string='Allowed Modules',
        help='Comma-separated technical names of the menu-owning modules '
             'licensed by the plan, e.g. "crm,sale,account". Use the module '
             'that owns the app\'s ROOT MENU xml-id (sale, not '
             'sale_management). Empty = no filtering (fail-open).',
    )
    plan_code = fields.Char(
        help='Plan code as defined on the platform (e.g. STARTER).',
    )
    subscription_status = fields.Char(
        help='Subscription status pushed by the platform (e.g. trial, '
             'active, suspended, expired). Deliberately a free Char: the '
             'tenant addon must not couple to platform-layer enums.',
    )
    max_users = fields.Integer(
        help='Maximum number of ACTIVE internal users allowed by the plan, '
             'pushed by the platform (P2-T01/P2-T03 contract, like the other '
             'fields here). 0 or unset = unlimited (fail-open).',
    )

    @api.model_create_multi
    def create(self, vals_list):
        # Access first, invariants second: an unauthorized caller must get
        # AccessError, never a ValidationError that leaks record state.
        self.browse().check_access('create')
        if self.search_count([]) + len(vals_list) > 1:
            raise ValidationError(self.env._(
                'Only one workspace configuration record may exist per '
                'tenant database.'
            ))
        records = super().create(vals_list)
        self.env.registry.clear_cache()
        return records

    def write(self, vals):
        result = super().write(vals)
        # Menu visibility (and its ormcached layers, including
        # ir.ui.menu.load_menus) must recompute after any config change.
        self.env.registry.clear_cache()
        return result

    def unlink(self):
        result = super().unlink()
        self.env.registry.clear_cache()
        return result

    @api.model
    def get_max_users(self):
        """Plan's max active internal users; 0 = unlimited."""
        config = self.sudo().get_config()
        return config.max_users if config else 0

    @api.model
    def get_config(self):
        """Return the singleton config record (empty recordset if absent)."""
        return self.search([], limit=1)

    @api.model
    def sync_from_platform(self, vals):
        """Single entry point for the platform config-sync channel (P2-T03).

        Called over json2/bearer by the dedicated, workspace.config-scoped
        service account. Whitelists the pushable fields (so even a compromised
        service account cannot write anything unexpected on this model), then
        updates the singleton — whose write() clears the tenant registry cache,
        so menu visibility + license enforcement recompute within the SLA.
        Returns a small status dict (json2-serialisable) for the caller to log.
        """
        if not isinstance(vals, dict):
            raise UserError(self.env._("sync_from_platform expects a dict of values."))
        rejected = set(vals) - _SYNCABLE_FIELDS
        if rejected:
            raise UserError(self.env._(
                "Refusing to sync non-whitelisted field(s): %s", ', '.join(sorted(rejected))))
        config = self.get_config()
        if not config:
            # The singleton is created once by provisioning; the write-scoped
            # sync account cannot create it. Surface loudly so the platform logs
            # it and the reconcile/seed path repairs it.
            raise UserError(self.env._(
                "No workspace configuration exists to sync into (not provisioned?)."))
        config.write({k: vals[k] for k in vals})
        return {'ok': True, 'plan_code': config.plan_code,
                'subscription_status': config.subscription_status}

    def get_allowed_module_list(self):
        """Parse allowed_module_names into a clean, de-duplicated list.

        Same semantics as ncollection.subscription.plan (P1-T07):
        whitespace stripped, empties dropped, order preserved, dupes removed.
        """
        self.ensure_one()
        if not self.allowed_module_names:
            return []
        seen = set()
        result = []
        for raw in self.allowed_module_names.split(','):
            name = raw.strip()
            if name and name not in seen:
                seen.add(name)
                result.append(name)
        return result
