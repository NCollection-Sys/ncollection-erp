from odoo import api, fields, models
from odoo.exceptions import ValidationError


class SubscriptionPlan(models.Model):
    _name = 'ncollection.subscription.plan'
    _description = 'NCollection Subscription Plan'
    _order = 'monthly_price asc'

    name = fields.Char(required=True)
    code = fields.Char(required=True, copy=False)
    monthly_price = fields.Monetary(string='Monthly Price')
    yearly_price = fields.Monetary(string='Yearly Price')
    max_users = fields.Integer(string='Max Users', default=1)
    max_companies = fields.Integer(string='Max Companies', default=1)
    active = fields.Boolean(default=True)
    description = fields.Text()
    # Comma-separated technical module names (e.g. "crm,sale,stock").
    # Deliberately a Text field, NOT a Many2many to ir.module.module: the
    # platform DB must not couple to any tenant DB's module registry
    # (database-per-tenant isolation - see DELIVERABLE_1 P1-T07).
    allowed_module_names = fields.Text(
        string='Allowed Modules',
        help='Comma-separated technical module names included in this plan, '
             'e.g. "crm,sale,stock". Consumed by the tenant workspace '
             'visibility engine (P1-T09) and license enforcement (P1-T10).',
    )
    currency_id = fields.Many2one(
        'res.currency',
        default=lambda self: self.env.company.currency_id,
    )
    tenant_ids = fields.One2many('ncollection.tenant', 'plan_id', string='Tenants')
    subscription_ids = fields.One2many('ncollection.subscription', 'plan_id', string='Subscriptions')
    tenant_count = fields.Integer(compute='_compute_tenant_count', string='Tenants')

    def _compute_tenant_count(self):
        for plan in self:
            plan.tenant_count = len(plan.tenant_ids)

    @api.constrains('max_users')
    def _check_max_users(self):
        for plan in self:
            if plan.max_users <= 0:
                raise ValidationError(
                    self.env._('Max Users must be strictly positive (plan "%s").', plan.name)
                )

    def get_allowed_module_list(self):
        """Return the plan's allowed modules as a clean, de-duplicated list.

        Parses ``allowed_module_names`` ("crm, sale,stock") into
        ``['crm', 'sale', 'stock']`` - whitespace stripped, empties dropped,
        order preserved, duplicates removed.
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

    _sql_constraints = [
        ('code_unique', 'unique(code)', 'The plan code must be unique.'),
    ]
