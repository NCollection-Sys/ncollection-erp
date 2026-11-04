# -*- coding: utf-8 -*-
"""The evidence that a payment was RECORDED AGAINST a document (the basis of an invoice-specific excess).

A payment applied to one invoice with an unapplied remainder proves nothing by itself: the same shape is an advance a
user later assigned to the invoice with the outstanding-credits widget, whose remainder is customer credit by intent.
What distinguishes "this customer paid MORE than this invoice" is how the payment came to be: Odoo's Register Payment
on that document, for an amount the user typed. That wizard is the only place we stamp the documents it was recorded
against; a payment created any other way (a manual payment, a bank import, an advance) carries no stamp, so its
remainder stays unallocated customer credit and is never shown as an invoice's negative balance.

Payments made before this stamp existed carry none: their remainder is credit, not an invented overpayment."""
from odoo import fields, models


class AccountPayment(models.Model):
    _inherit = 'account.payment'

    nc_registered_move_ids = fields.Many2many(
        'account.move', 'nc_payment_registered_move_rel', 'payment_id', 'move_id', string='Recorded Against',
        copy=False, readonly=True, help="The documents this payment was recorded against with Register Payment.")
