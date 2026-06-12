from odoo import fields, models


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

    _sql_constraints = [
        ('code_unique', 'unique(code)', 'The plan code must be unique.'),
    ]
