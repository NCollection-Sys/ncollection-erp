# -*- coding: utf-8 -*-
"""Request for quotation / purchase order: Duplicate, Files, and View = Print.

The new record comes from the model's own ``copy()`` (a new number, the draft RFQ state, no receipts, no bills, no
chatter history) and runs as the calling user, so it needs the same right as "New".

View (and the form's Preview, which Odoo does not have for a purchase) opens the very PDF Print produces: the
"Request for Quotation" while the order is still an RFQ (Odoo's Print RFQ), the "Purchase Order" once confirmed
(the order's Print), through nc.document.print.mixin.
"""
from odoo import models

_RFQ_STATES = ('draft', 'sent', 'to approve')


class PurchaseOrder(models.Model):
    _inherit = ['purchase.order', 'nc.duplicate.mixin', 'nc.partner.roles.mixin',   # roles: Customer / Vendor badges
                'nc.attachment.mixin', 'nc.document.print.mixin']
    _name = 'purchase.order'

    def _nc_print_report(self):
        self.ensure_one()
        if self.state in _RFQ_STATES:
            return self.env.ref('purchase.report_purchase_quotation')
        return self.env.ref('purchase.action_report_purchase_order')
