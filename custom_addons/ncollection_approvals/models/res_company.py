# -*- coding: utf-8 -*-
from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    # SO approval threshold (P3-T07): a sale order whose total exceeds this needs
    # a Sales Manager's approval before it can be confirmed. 0 disables the gate.
    nc_sale_approval_threshold = fields.Monetary(
        string='Sale Approval Threshold',
        currency_field='currency_id',
        default=0.0,
        help='Sale orders above this total require manager approval before they '
             'can be confirmed. Set to 0 to disable the gate.')
    # Purchase two-level approval (P3-T07): a PO above this total needs
    # department THEN finance approval before it can be confirmed. 0 disables it.
    nc_purchase_approval_threshold = fields.Monetary(
        string='Purchase Approval Threshold',
        currency_field='currency_id',
        default=0.0,
        help='Purchase orders above this total require two-level (department, '
             'then finance) approval before confirmation. Set to 0 to disable.')
