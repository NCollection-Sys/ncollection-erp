from odoo import fields, models


class Module(models.Model):
    _name = 'ncollection.module'
    _description = 'NCollection SaaS Module Catalog'
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

    tenant_ids = fields.Many2many(
        'ncollection.tenant',
        'ncollection_tenant_module_rel',
        'module_id',
        'tenant_id',
        string='Tenants',
    )
    tenant_count = fields.Integer(compute='_compute_tenant_count')

    def _compute_tenant_count(self):
        for module in self:
            module.tenant_count = len(module.tenant_ids)

    _sql_constraints = [
        ('technical_name_unique', 'unique(technical_name)', 'The technical name must be unique.'),
    ]
