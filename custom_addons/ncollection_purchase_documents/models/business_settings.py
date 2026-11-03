# -*- coding: utf-8 -*-
"""Purchase's part of the Workspace Administration Center's Business Settings under Option A (see ncollection_core's
business_settings.py). Two purchase settings are not fields Odoo stores directly: its settings page turns "Lock
Confirmed Orders" and "Purchase Order Approval" into the company's ``po_lock`` and ``po_double_validation``
(purchase/models/res_config_settings.py ``set_values``); the same translation is done here."""
from odoo import api, fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    @api.model
    def _nc_tenant_admin_company_fields(self):
        return super()._nc_tenant_admin_company_fields() | {
            'po_lock', 'po_double_validation', 'po_double_validation_amount',
        }


class NcBusinessSettings(models.TransientModel):
    _inherit = 'ncollection.business.settings'

    company_currency_id = fields.Many2one(related='company_id.currency_id', string='Company Currency')
    lock_confirmed_po = fields.Boolean(string='Lock Confirmed Orders')
    po_order_approval = fields.Boolean(string='Purchase Order Approval')
    po_double_validation_amount = fields.Monetary(string='Minimum Amount', currency_field='company_currency_id')
    group_warning_purchase = fields.Boolean(string='Purchase Warnings')
    group_send_reminder = fields.Boolean(string='Receipt Reminder')

    @api.model
    def _nc_settings_spec(self):
        spec = super()._nc_settings_spec()
        spec.update({
            'lock_confirmed_po': '_nc_apply_po_lock',
            'po_order_approval': '_nc_apply_po_approval',
            'po_double_validation_amount': 'native',
            'group_warning_purchase': 'native',
            'group_send_reminder': 'native',
        })
        return spec

    def _nc_apply_po_lock(self, company, value):
        company.write({'po_lock': 'lock' if value else 'edit'})

    def _nc_apply_po_approval(self, company, value):
        company.write({'po_double_validation': 'two_step' if value else 'one_step'})
