from odoo import fields, models


class Module(models.Model):
    """DEAD CODE. This model does not load, and it licenses nothing (#455).

    READ THIS BEFORE LOADING OR WIRING IT. Two independent facts, both verified:

    1. **It never loads.** ``models/__init__.py`` does not import this file and
       ``module_views.xml`` is not in the manifest, so neither the model nor
       its kanban of module toggles exists at runtime.
    2. **Even if it did, it would grant nothing.** Its ``tenant_ids`` M2M has
       no counterpart field on ``ncollection.tenant`` and no reader anywhere in
       provisioning, config sync or ncollection_core.

    What a tenant may actually use is decided exclusively by
    ``ncollection.subscription.plan.allowed_module_names``: provisioning
    installs CORE_TENANT_MODULES + that list, and config sync pushes it into
    each tenant's ``ncollection.workspace.config``, where Ring 1 (menus) and
    Ring 2 (ORM) enforce it. #455 surfaced that field in the admin UI instead
    of resurrecting this catalog, precisely because a second place to "manage
    modules" that reaches no tenant is worse than none.

    The file is kept rather than deleted because a human-readable catalog
    (display name, category, icon, description) is genuinely useful for pricing
    pages and for composing plan module lists. If per-tenant module selection is
    ever wanted, the honest design is a field ON the tenant that feeds
    ``allowed_module_names`` through the existing sync — not making this catalog
    authoritative behind the admin's back.

    ``test_tenant_module_management.test_the_module_catalog_model_is_not_loaded_at_all``
    fails the moment this is loaded, so that becomes a deliberate decision.
    """
    _name = 'ncollection.module'
    _description = 'NCollection Module Catalog (presentation only — see the plan for licensing)'
    _order = 'sequence, name'

    name = fields.Char(required=True)
    technical_name = fields.Char(string='Technical Name', required=True, copy=False)
    category = fields.Selection(
        selection=[
            ('core', 'Core'),
            ('sales', 'Sales'),
            ('finance', 'Finance'),
            ('inventory', 'Inventory'),
            ('hr', 'HR'),
            ('manufacturing', 'Manufacturing'),
            ('services', 'Services'),
            ('marketing', 'Marketing'),
            ('other', 'Other'),
        ],
        default='other',
        required=True,
    )
    description = fields.Text()
    icon = fields.Char(
        string='Icon',
        default='fa-cube',
        help='FontAwesome icon class (e.g. fa-cube, fa-cog, fa-shopping-cart).',
    )
    is_default = fields.Boolean(
        string='Default',
        help='When checked, this module is pre-selected for new tenants.',
    )
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    # NOT a licensing link (#455). Nothing reads this relation — see the class
    # docstring. Labelled so the UI cannot be mistaken for module management.
    tenant_ids = fields.Many2many(
        'ncollection.tenant',
        'ncollection_tenant_module_rel',
        'module_id',
        'tenant_id',
        string='Tenants (reference only)',
        help="Bookkeeping for the catalog. This does NOT grant the module to a "
             "tenant: licensing comes from the subscription plan's module list.",
    )
    tenant_count = fields.Integer(compute='_compute_tenant_count')

    def _compute_tenant_count(self):
        for module in self:
            module.tenant_count = len(module.tenant_ids)

    _sql_constraints = [
        ('technical_name_unique', 'unique(technical_name)', 'The technical name must be unique.'),
    ]
