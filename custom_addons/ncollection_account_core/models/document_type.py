# -*- coding: utf-8 -*-
"""The accounting documents' optional print section, as a Print Setting of their document type."""
from odoo import api, fields, models

_MOVE_KINDS = ('customer_invoice', 'customer_credit_note', 'vendor_bill', 'vendor_credit_note')


class NcDocumentType(models.Model):
    _inherit = 'ncollection.document.type'

    nc_show_payment_status = fields.Boolean(
        string='Payment status', default=True,
        help="Print the paid amount and the payment status (Unpaid, Partially Paid, Paid in Full...) under the totals "
             "of this document.")

    @api.model
    def _nc_print_setting_fields(self):
        return super()._nc_print_setting_fields() | {'nc_show_payment_status'}


class AccountMove(models.Model):
    _inherit = 'account.move'

    def _nc_print_shows_payment_status(self):
        """The Print Setting of this move's kind of document; shown when no kind says otherwise."""
        self.ensure_one()
        report = self.env.ref('account.account_invoices', raise_if_not_found=False)
        kinds = self.env['ncollection.document.type'].sudo()._nc_types_for(report, self) if report else False
        return not kinds or any(kinds.mapped('nc_show_payment_status'))
