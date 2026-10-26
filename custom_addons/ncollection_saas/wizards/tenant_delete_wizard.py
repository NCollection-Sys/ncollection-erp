# -*- coding: utf-8 -*-
"""The Delete Tenant dialog (models/tenant_deletion.py holds the run, the checks and the audit)."""
from odoo import api, fields, models


class TenantDeleteWizard(models.TransientModel):
    _name = 'ncollection.tenant.delete.wizard'
    _description = 'Delete Tenant'

    tenant_id = fields.Many2one('ncollection.tenant', required=True, readonly=True, ondelete='cascade')
    company_name = fields.Char(related='tenant_id.company_name')
    database_name = fields.Char(related='tenant_id.database_name', string='Identifier')
    tenant_url = fields.Char(related='tenant_id.tenant_url')
    confirm_identifier = fields.Char(string='Type the identifier to confirm')

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        if self.env.context.get('active_model') == 'ncollection.tenant' and self.env.context.get('active_id'):
            values.setdefault('tenant_id', self.env.context['active_id'])
        return values

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for wizard in records:
            self.env['ncollection.tenant.deletion']._nc_assert_may_delete(wizard.tenant_id)
        return records

    def action_delete(self):
        self.ensure_one()
        record = self.env['ncollection.tenant.deletion']._nc_request(self.tenant_id, self.confirm_identifier)
        return record._nc_result_action()
