# -*- coding: utf-8 -*-
"""Provision-a-sub-tenant wizard (P10-T09) — admin DB.

Thin UI over ncollection.reseller.provision_subtenant so a partner can launch a
branded sub-tenant from the reseller dashboard. All the real work (quota check,
provisioning trigger, brand cascade) lives on the model; this only collects the
inputs.
"""

from odoo import fields, models


class ResellerProvisionWizard(models.TransientModel):
    _name = 'ncollection.reseller.provision.wizard'
    _description = 'Provision Sub-Tenant Wizard'

    reseller_id = fields.Many2one(
        'ncollection.reseller', required=True,
        default=lambda self: self.env.context.get('active_id'))
    company_name = fields.Char(required=True)
    subdomain = fields.Char(string='Workspace Address', required=True)
    plan_id = fields.Many2one(
        'ncollection.subscription.plan', required=True,
        domain=[('active', '=', True)])
    email = fields.Char(string='Contact Email')
    contact_name = fields.Char(string='Contact Name')
    billing_cycle = fields.Selection(
        [('monthly', 'Monthly'), ('yearly', 'Yearly')],
        default='monthly', required=True)

    def action_provision(self):
        self.ensure_one()
        tenant = self.reseller_id.provision_subtenant(
            company_name=self.company_name,
            subdomain=self.subdomain,
            plan=self.plan_id,
            email=self.email,
            contact_name=self.contact_name,
            billing_cycle=self.billing_cycle,
        )
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'ncollection.tenant',
            'res_id': tenant.id,
            'view_mode': 'form',
            'target': 'current',
        }
