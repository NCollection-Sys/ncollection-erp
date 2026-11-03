# -*- coding: utf-8 -*-
"""Sales' part of the Workspace Administration Center's Business Settings under Option A (see ncollection_core's
business_settings.py): the sales settings a workspace Owner changes, applied where Odoo itself stores them."""
from odoo import api, fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    @api.model
    def _nc_tenant_admin_company_fields(self):
        return super()._nc_tenant_admin_company_fields() | {
            'quotation_validity_days', 'portal_confirmation_sign', 'portal_confirmation_pay', 'prepayment_percent',
        }


class NcBusinessSettings(models.TransientModel):
    _inherit = 'ncollection.business.settings'

    quotation_validity_days = fields.Integer(string='Default Quotation Validity (days)')
    portal_confirmation_sign = fields.Boolean(string='Online Signature')
    portal_confirmation_pay = fields.Boolean(string='Online Payment')
    prepayment_percent = fields.Float(string='Prepayment Percentage')
    group_discount_per_so_line = fields.Boolean(string='Discounts')
    group_proforma_sales = fields.Boolean(string='Pro-Forma Invoice')
    automatic_invoice = fields.Boolean(string='Automatic Invoice')

    @api.model
    def _nc_settings_spec(self):
        spec = super()._nc_settings_spec()
        spec.update(dict.fromkeys((
            'quotation_validity_days', 'portal_confirmation_sign', 'portal_confirmation_pay', 'prepayment_percent',
            'group_discount_per_so_line', 'group_proforma_sales', 'automatic_invoice'), 'native'))
        return spec
