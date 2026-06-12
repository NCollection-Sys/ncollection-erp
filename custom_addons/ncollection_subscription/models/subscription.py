from odoo import fields, models


class Subscription(models.Model):
    _name = 'ncollection.subscription'
    _description = 'NCollection Subscription'
    _order = 'start_date desc'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(required=True, copy=False, default='New')
    tenant_id = fields.Many2one('ncollection.tenant', string='Tenant', required=True, tracking=True)
    plan_id = fields.Many2one('ncollection.subscription.plan', string='Plan', required=True, tracking=True)
    start_date = fields.Date(required=True, default=fields.Date.context_today)
    end_date = fields.Date()
    billing_cycle = fields.Selection(
        selection=[
            ('monthly', 'Monthly'),
            ('yearly', 'Yearly'),
        ],
        default='monthly',
        required=True,
    )
    status = fields.Selection(
        selection=[
            ('draft', 'Draft'),
            ('active', 'Active'),
            ('expired', 'Expired'),
            ('cancelled', 'Cancelled'),
        ],
        default='draft',
        required=True,
        tracking=True,
    )
