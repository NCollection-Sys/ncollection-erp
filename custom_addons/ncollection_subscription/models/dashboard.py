from datetime import timedelta

from odoo import api, fields, models


class SubscriptionDashboard(models.TransientModel):
    _name = 'ncollection.subscription.dashboard'
    _description = 'NCollection SaaS Dashboard'

    total_tenants = fields.Integer(compute='_compute_kpis')
    active_tenants = fields.Integer(compute='_compute_kpis')
    trial_tenants = fields.Integer(compute='_compute_kpis')
    active_subscriptions = fields.Integer(compute='_compute_kpis')
    monthly_revenue = fields.Monetary(compute='_compute_kpis')
    expiring_soon = fields.Integer(compute='_compute_kpis')
    currency_id = fields.Many2one('res.currency', default=lambda self: self.env.company.currency_id)

    @api.depends_context('uid')
    def _compute_kpis(self):
        tenant_model = self.env['ncollection.tenant']
        subscription_model = self.env['ncollection.subscription']

        total_tenants = tenant_model.search_count([])
        active_tenants = tenant_model.search_count([('status', '=', 'active')])
        trial_tenants = tenant_model.search_count([('status', '=', 'trial')])

        active_subscriptions_recs = subscription_model.search([('status', '=', 'active')])
        active_subscriptions = len(active_subscriptions_recs)

        # Demo-level estimate: sum of active subscriptions' plan price,
        # normalized to a monthly amount (yearly price / 12).
        monthly_revenue = 0.0
        for sub in active_subscriptions_recs:
            if not sub.plan_id:
                continue
            if sub.billing_cycle == 'yearly':
                monthly_revenue += sub.plan_id.yearly_price / 12.0
            else:
                monthly_revenue += sub.plan_id.monthly_price

        soon = fields.Date.context_today(self) + timedelta(days=30)
        expiring_soon = subscription_model.search_count([
            ('status', '=', 'active'),
            ('end_date', '!=', False),
            ('end_date', '<=', soon),
        ])

        for record in self:
            record.total_tenants = total_tenants
            record.active_tenants = active_tenants
            record.trial_tenants = trial_tenants
            record.active_subscriptions = active_subscriptions
            record.monthly_revenue = monthly_revenue
            record.expiring_soon = expiring_soon
