from dateutil.relativedelta import relativedelta

from odoo import fields, models


class TenantWizard(models.TransientModel):
    _name = 'ncollection.tenant.wizard'
    _description = 'NCollection Tenant Creation Wizard'

    company_name = fields.Char(string='Company Name', required=True)
    contact_name = fields.Char(string='Contact Name')
    email = fields.Char(string='Email')
    phone = fields.Char(string='Phone')
    database_name = fields.Char(string='Database Name', required=True)
    subdomain = fields.Char(string='Subdomain')
    plan_id = fields.Many2one('ncollection.subscription.plan', string='Subscription Plan', required=True)
    billing_cycle = fields.Selection(
        selection=[
            ('monthly', 'Monthly'),
            ('yearly', 'Yearly'),
        ],
        default='monthly',
        required=True,
    )
    module_ids = fields.Many2many(
        'ncollection.module',
        string='Modules to Provision',
        default=lambda self: self.env['ncollection.module'].search([('is_default', '=', True)]),
    )

    def action_create_tenant(self):
        self.ensure_one()
        tenant = self.env['ncollection.tenant'].create({
            'company_name': self.company_name,
            'contact_name': self.contact_name,
            'email': self.email,
            'phone': self.phone,
            'database_name': self.database_name,
            'domain': self.subdomain,
            'plan_id': self.plan_id.id,
            'status': 'trial',
            'database_status': 'provisioning',
            'module_ids': [(6, 0, self.module_ids.ids)],
        })

        today = fields.Date.context_today(self)
        end_date = today + (
            relativedelta(years=1) if self.billing_cycle == 'yearly'
            else relativedelta(months=1)
        )
        subscription = self.env['ncollection.subscription'].create({
            'name': f'SUB-{tenant.id:04d}',
            'tenant_id': tenant.id,
            'plan_id': self.plan_id.id,
            'billing_cycle': self.billing_cycle,
            'start_date': today,
            'end_date': end_date,
            'status': 'active',
        })

        tenant.subscription_id = subscription.id

        self.env['ncollection.provisioning.job'].create({
            'tenant_id': tenant.id,
            'database_name': self.database_name,
            'status': 'queued',
            'log': 'Provisioning queued via Tenant Wizard',
            'module_ids': [(6, 0, self.module_ids.ids)],
        })

        return {
            'type': 'ir.actions.act_window',
            'name': 'Tenant',
            'res_model': 'ncollection.tenant',
            'res_id': tenant.id,
            'view_mode': 'form',
            'target': 'current',
        }
