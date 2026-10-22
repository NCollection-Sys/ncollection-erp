# -*- coding: utf-8 -*-
"""Gives every QWeb template the product identity, with or without an HTTP request (pages, error layouts, previews)."""
from odoo import models


class IrQweb(models.AbstractModel):
    _inherit = 'ir.qweb'

    def _prepare_environment(self, values):
        irqweb = super()._prepare_environment(values)
        # a callable, so the one-row lookup happens only in templates that use it
        values.setdefault('nc_product_brand', irqweb.env['ncollection.product.branding'].nc_brand)
        return irqweb
