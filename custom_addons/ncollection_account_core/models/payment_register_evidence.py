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


class AccountPaymentRegister(models.TransientModel):
    _inherit = 'account.payment.register'

    def _create_payments(self):
        payments = super()._create_payments()
        wizard_moves = self.line_ids.move_id
        moves = self.env['account.move']
        for payment in payments.sudo():
            against = (payment.reconciled_invoice_ids | payment.reconciled_bill_ids) & wizard_moves
            if against:
                payment.nc_registered_move_ids = [(6, 0, against.ids)]
                moves |= against
        if moves:
            self.env.add_to_compute(moves._fields['nc_overpaid_amount'], moves.sudo())
            moves.sudo().flush_recordset(['nc_overpaid_amount'])
        return payments
