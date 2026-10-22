# -*- coding: utf-8 -*-
from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    nc_product_name = fields.Char(compute='_compute_nc_product_name')

    def _compute_nc_product_name(self):
        name = self.env['ncollection.product.branding'].nc_brand()['name']
        for settings in self:
            settings.nc_product_name = name

    # Odoo's Languages block shows only a COUNT ("2 Languages") and two links,
    # so a user cannot tell WHICH languages are installed without opening the
    # list. This is a read-only view of the real, active res.lang records — the
    # same set the preferences dialog offers — not a second language registry.
    nc_active_language_ids = fields.Many2many(
        'res.lang', string='Installed languages',
        compute='_compute_nc_active_language_ids', readonly=True)

    @api.depends_context('lang')
    def _compute_nc_active_language_ids(self):
        langs = self.env['res.lang'].search([('active', '=', True)])
        for settings in self:
            settings.nc_active_language_ids = langs
