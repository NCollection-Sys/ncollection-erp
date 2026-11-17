# -*- coding: utf-8 -*-
"""Inventory Settings of the unified Settings (Business Settings page, Inventory section; see ncollection_core's
business_settings.py). It lives in this module because this is the NCollection module every tenant with Inventory
gets (auto_install with stock); it has nothing to do with scanning.

Only settings whose whole effect is the feature group or the company field are offered. Storage Locations and
Multi-Step Routes are NOT: Odoo's settings page also (de)activates warehouse operation types and views when they
change (stock/models/res_config_settings.py ``set_values``), and doing half of that would leave warehouses
inconsistent. Lots & Serial Numbers keeps Odoo's own refusal to switch off while tracked products exist."""
from odoo import api, fields, models
from odoo.exceptions import UserError

INVENTORY = (
    'group_stock_production_lot', 'group_lot_on_delivery_slip', 'group_stock_tracking_lot',
    'group_stock_tracking_owner', 'group_warning_stock', 'group_stock_sign_delivery', 'group_stock_reception_report',
    'annual_inventory_month', 'annual_inventory_day',
)


def _native_selection(name):
    def selection(self):
        return self.env['res.config.settings']._fields[name]._description_selection(self.env)
    return selection


class NcBusinessSettings(models.TransientModel):
    _inherit = 'ncollection.business.settings'

    group_stock_production_lot = fields.Boolean(string='Lots & Serial Numbers')
    group_lot_on_delivery_slip = fields.Boolean(string='Display Lots & Serial Numbers on Delivery Slips')
    group_stock_tracking_lot = fields.Boolean(string='Packages')
    group_stock_tracking_owner = fields.Boolean(string='Consignment')
    group_warning_stock = fields.Boolean(string='Warnings for Stock')
    group_stock_sign_delivery = fields.Boolean(string='Signature')
    group_stock_reception_report = fields.Boolean(string='Reception Report')
    annual_inventory_month = fields.Selection(_native_selection('annual_inventory_month'),
                                              string='Annual Inventory Month')
    annual_inventory_day = fields.Integer(string='Day of the month')

    @api.model
    def _nc_settings_spec(self):
        spec = super()._nc_settings_spec()
        spec.update(dict.fromkeys(INVENTORY, 'native'))
        spec['group_stock_production_lot'] = '_nc_apply_lots'
        return spec

    @api.model
    def _nc_setting_sections(self):
        sections = super()._nc_setting_sections()
        sections.update(dict.fromkeys(INVENTORY, 'inventory'))
        return sections

    def _nc_apply_lots(self, company, value):
        if not value and self.env['product.product'].sudo().search_count([('tracking', '!=', 'none')], limit=1):
            raise UserError(self.env._(
                "You have product(s) in stock that have lot/serial number tracking enabled. \nSwitch off tracking on "
                "all the products before switching off this setting."))
        self._nc_apply_native(company, 'group_stock_production_lot', value)
        if not value:
            self._nc_apply_native(company, 'group_lot_on_delivery_slip', False)
