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
from odoo.exceptions import ValidationError


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
    def get_config(self):
        """Return the singleton config record (empty recordset if absent)."""
        return self.search([], limit=1)

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
