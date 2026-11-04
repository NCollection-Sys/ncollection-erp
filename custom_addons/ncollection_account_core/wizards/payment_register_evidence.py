# -*- coding: utf-8 -*-
"""Stamps the documents a payment was recorded against (see models/payment_register_evidence.py)."""
from odoo import models


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
