from odoo import fields, models


class ProvisioningJob(models.Model):
    _name = 'ncollection.provisioning.job'
    _description = 'NCollection Provisioning Job'
    _order = 'created_at desc'

    tenant_id = fields.Many2one('ncollection.tenant', string='Tenant', required=True, ondelete='cascade')
    database_name = fields.Char(string='Database Name', required=True)
    status = fields.Selection(
        selection=[
            ('queued', 'Queued'),
            ('running', 'Running'),
            ('done', 'Done'),
            ('failed', 'Failed'),
        ],
        default='queued',
        required=True,
    )
    created_at = fields.Datetime(string='Created At', default=fields.Datetime.now, required=True)
    completed_at = fields.Datetime(string='Completed At')
    log = fields.Text(string='Log')
