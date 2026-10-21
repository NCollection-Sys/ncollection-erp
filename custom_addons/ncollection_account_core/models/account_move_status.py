# -*- coding: utf-8 -*-
"""The payment status of an invoice / bill / credit note / receipt, read from Odoo's own accounting.

Nothing here is stored and nothing is maintained by hand: every value is computed, at read time, from the native
fields Odoo's reconciliation already keeps up to date -- ``amount_total``, ``amount_residual``, ``invoice_date_due``
and the partial reconciliations (``account.partial.reconcile``) of the document's receivable / payable lines.

    Unpaid          posted, an amount is due, nothing applied yet, not past due
    Partially Paid  posted, something applied, an amount still due, not past due
    Overdue         posted, an amount still due and the due date has passed (whether or not part was paid)
    Paid in Full    posted, nothing left due
    Overpaid        posted, nothing left due, and a payment / credit that settled it still has an UNAPPLIED
                    remainder (the excess is still sitting on the customer's / vendor's account)

A draft, a cancelled document, a reversed one and a plain journal entry have no payment status (empty).
"""
from odoo import api, fields, models

NC_PAYMENT_STATUSES = [
    ('unpaid', 'Unpaid'),
    ('partial', 'Partially Paid'),
    ('overdue', 'Overdue'),
    ('paid', 'Paid in Full'),
    ('overpaid', 'Overpaid'),
]
_PAY_TERM_TYPES = ('asset_receivable', 'liability_payable')


class AccountMove(models.Model):
    _inherit = 'account.move'

    nc_amount_paid = fields.Monetary(
        string='Paid Amount', compute='_compute_nc_payment_status', currency_field='currency_id',
        help="What has been applied to this document so far (total minus amount due), in its own currency.")
    nc_amount_due = fields.Monetary(
        string='Balance Due', compute='_compute_nc_payment_status', currency_field='currency_id',
        help="What is still due on this document, in its own currency (the residual amount).")
    nc_payment_status = fields.Selection(
        NC_PAYMENT_STATUSES, string='Settlement Status', compute='_compute_nc_payment_status',
        help="Derived from the accounting (residual, due date and reconciliations); never set by hand.")

    @api.depends('state', 'move_type', 'amount_total', 'amount_residual', 'invoice_date_due', 'payment_state',
                 'line_ids.matched_debit_ids', 'line_ids.matched_credit_ids', 'line_ids.amount_residual')
    @api.depends_context('tz')                      # "today" (for Overdue) is the user's day
    def _compute_nc_payment_status(self):
        today = fields.Date.context_today(self)
        for move in self:
            move.nc_amount_due = move.amount_residual if move.is_invoice(include_receipts=True) else 0.0
            move.nc_amount_paid = (move.amount_total - move.amount_residual
                                   if move.is_invoice(include_receipts=True) else 0.0)
            move.nc_payment_status = move._nc_payment_status_value(today)

    def _nc_payment_status_value(self, today):
        self.ensure_one()
        if (self.state != 'posted' or not self.is_invoice(include_receipts=True)
                or self.payment_state in ('reversed', 'invoicing_legacy')):
            return False
        currency = self.currency_id
        if not currency.is_zero(self.amount_residual):
            if self.invoice_date_due and self.invoice_date_due < today:
                return 'overdue'
            return 'partial' if currency.compare_amounts(self.amount_residual, self.amount_total) < 0 else 'unpaid'
        return 'overpaid' if self._nc_has_unapplied_settlement() else 'paid'

    def _nc_has_unapplied_settlement(self):
        """True when a journal item reconciled with this document (a payment, a credit note...) still has a
        remainder of its own: the document took less than that payment / credit brought.

        Reading reconciliations needs the right to read them. A user who may read invoices without that right (a
        salesman following an order's invoices) must still be able to open the list, so for that user a settled
        document simply reads "Paid in Full": the Overpaid distinction is an accounting figure."""
        self.ensure_one()
        if not (self.env['account.partial.reconcile'].has_access('read')
                and self.env['account.move.line'].has_access('read')):
            return False
        lines = self.line_ids.filtered(lambda line: line.account_id.account_type in _PAY_TERM_TYPES)
        partials = lines.matched_debit_ids | lines.matched_credit_ids
        counterparts = (partials.debit_move_id | partials.credit_move_id) - lines
        return any(
            not line.reconciled and not (line.company_currency_id.is_zero(line.amount_residual)
                                         and line.currency_id.is_zero(line.amount_residual_currency))
            for line in counterparts)
