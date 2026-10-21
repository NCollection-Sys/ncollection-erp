# -*- coding: utf-8 -*-
"""Hands the product identity to the web client (tab title, error dialogs) from the same source as the server
templates."""
from odoo import models


class IrHttp(models.AbstractModel):
    _inherit = 'ir.http'

    def session_info(self):
        info = super().session_info()
        info['nc_product_brand'] = self.env['ncollection.product.branding'].nc_brand()
        return info
