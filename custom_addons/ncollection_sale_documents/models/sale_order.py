# -*- coding: utf-8 -*-
"""Quotation / sales order: Duplicate, and Preview that shows the document Print produces.

Odoo's Preview opens the customer-portal page of the order, which is a DIFFERENT template from the printed
quotation (``sale.report_saleorder``): what the user previewed was never what the PDF contained. Here Preview opens
the same report with the same context as Print (served inline by ncollection_core's ``/ncollection/document/view``
endpoint), so the two cannot differ.
"""
from odoo import models


class SaleOrder(models.Model):
    _inherit = ['sale.order', 'nc.duplicate.mixin', 'nc.partner.roles.mixin',   # roles: Customer / Vendor badges
                'nc.document.print.mixin']
    _name = 'sale.order'

    def _nc_print_report(self):
        """The report action Print uses for a quotation / order."""
        return self.env.ref('sale.action_report_saleorder')

    def action_preview_sale_order(self):
        """Preview = the printed quotation / order (nc.document.print.mixin), not the customer portal page."""
        return self.action_nc_view()
