# -*- coding: utf-8 -*-
"""Sales Settings of the unified Settings (the Workspace Administration Center's Business Settings page, Sales
section; see ncollection_core's business_settings.py): the sales settings a Workspace Administrator, or a delegate of
Sales Settings, changes, applied where Odoo itself stores them. Each field mirrors res.config.settings' own field of
the same name; none installs a module."""
from odoo import api, fields, models

SALES = (
    'quotation_validity_days', 'portal_confirmation_sign', 'portal_confirmation_pay', 'prepayment_percent',
    'group_discount_per_so_line', 'group_proforma_sales', 'automatic_invoice', 'default_invoice_policy',
    'group_auto_done_setting', 'group_warning_sale', 'group_uom', 'group_product_variant',
)


def _native_selection(name):
    def selection(self):
        return self.env['res.config.settings']._fields[name]._description_selection(self.env)
    return selection


class NcBusinessSettings(models.TransientModel):
    _inherit = 'ncollection.business.settings'

    quotation_validity_days = fields.Integer(string='Default Quotation Validity (days)')
    portal_confirmation_sign = fields.Boolean(string='Online Signature')
    portal_confirmation_pay = fields.Boolean(string='Online Payment')
    prepayment_percent = fields.Float(string='Prepayment Percentage')
    group_discount_per_so_line = fields.Boolean(string='Discounts')
    group_proforma_sales = fields.Boolean(string='Pro-Forma Invoice')
    automatic_invoice = fields.Boolean()
    default_invoice_policy = fields.Selection(_native_selection('default_invoice_policy'), string='Invoicing Policy')
    group_auto_done_setting = fields.Boolean(string='Lock Confirmed Sales')
    group_warning_sale = fields.Boolean(string='Sale Order Warnings')
    group_uom = fields.Boolean(string='Units of Measure & Packagings')
    group_product_variant = fields.Boolean(string='Variants')

    @api.model
    def _nc_settings_spec(self):
        spec = super()._nc_settings_spec()
        spec.update(dict.fromkeys(SALES, 'native'))
        spec['default_invoice_policy'] = '_nc_apply_invoice_policy'
        return spec

    @api.model
    def _nc_setting_sections(self):
        sections = super()._nc_setting_sections()
        sections.update(dict.fromkeys(SALES, 'sales'))
        return sections

    def _nc_apply_invoice_policy(self, company, value):
        """As Odoo's settings page: the product default, and automatic invoicing only with "invoice what is
        ordered" (sale/wizard/res_config_settings.py ``set_values``)."""
        self._nc_apply_native(company, 'default_invoice_policy', value)
        if value != 'order':
            self.env['ir.config_parameter'].sudo().set_param('sale.automatic_invoice', False)
