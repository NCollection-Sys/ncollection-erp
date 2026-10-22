# -*- coding: utf-8 -*-
"""Attachments of a quotation / sales order, for the list-view viewer.

The count and the metadata come from the shared ``nc.attachment.mixin`` (ncollection_core): Odoo's own
``ir.attachment`` rows of the order, read AS THE CALLING USER, so a user who cannot read an order cannot list, count
or open its files. The paperclip widget and the viewer are ncollection_core's too, shared with the other documents.
"""
from odoo import models


class SaleOrder(models.Model):
    _inherit = ['sale.order', 'nc.attachment.mixin']
    _name = 'sale.order'
