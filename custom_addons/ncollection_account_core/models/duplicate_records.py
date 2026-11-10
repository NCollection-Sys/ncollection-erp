# -*- coding: utf-8 -*-
"""Duplicate on products, payment terms and PAYMENTS: the model's own ``copy()``, wrapped by nc.duplicate.mixin.

A product's internal reference and barcode identify ONE product in a scan or a price list: the copy starts without
them (the barcode is already ``copy=False``). Taxes and journals are deliberately NOT offered a duplicate here: a tax
or an account is configuration the accountant creates on purpose.

Payments (customer and vendor) were left out of DUP-01 on the reasoning that a payment is money that moved once. The
owner's review asked for them: recording the next instalment, or the same supplier payment again, starts from the
last one. What makes that safe is that Odoo's own ``copy()`` of a payment already is "a new draft with the same
editable values": the state, the journal entry, the reconciliation, the bank-statement matching, the payment
reference and the sent flag are ``copy=False`` or computed from the (absent) entry. This module only adds what Odoo
cannot know."""
from odoo import fields, models


class ProductTemplate(models.Model):
    _name = 'product.template'
    _inherit = ['product.template', 'nc.duplicate.mixin']

    def _nc_duplicate_defaults(self):
        defaults = super()._nc_duplicate_defaults()
        defaults.update({'default_code': False, 'barcode': False})
        return defaults


class AccountPaymentTerm(models.Model):
    _name = 'account.payment.term'
    _inherit = ['account.payment.term', 'nc.duplicate.mixin']


class AccountPayment(models.Model):
    _name = 'account.payment'
    _inherit = ['account.payment', 'nc.duplicate.mixin']

    def _nc_duplicate_blocker(self):
        """Customer and vendor payments only. One half of an internal transfer is not a document on its own: a copy
        of it would be a payment with no counterpart."""
        if self.paired_internal_transfer_payment_id:
            return self.env._("This payment is one side of an internal transfer and cannot be duplicated.")
        if self.partner_type not in ('customer', 'supplier') or self.payment_type not in ('inbound', 'outbound'):
            return self.env._("Only customer and vendor payments can be duplicated.")
        return super()._nc_duplicate_blocker()

    def _nc_duplicate_defaults(self):
        """What a NEW payment must not inherit beyond Odoo's own ``copy=False`` fields:

        * the date: the copy is a payment to be made now, not a second entry in the original's period;
        * anything that identifies the ORIGINAL's execution: the online transaction and token, the refund link, the
          cheque number, the cheque print jobs (an append-only audit of what was printed) and the attachments
          (the original's remittance advice or bank slip is evidence of THAT payment);
        * an archived journal: the copy takes the company's first usable bank / cash journal and that journal's
          first payment method for the direction, instead of creating a draft nobody can confirm."""
        defaults = super()._nc_duplicate_defaults()
        defaults['date'] = fields.Date.context_today(self)
        for name in ('payment_transaction_id', 'payment_token_id', 'source_payment_id', 'check_number',
                     'nc_cheque_job_ids', 'attachment_ids'):
            if name in self._fields:
                defaults[name] = False
        if not self.journal_id.active:
            journal = self.env['account.journal'].search([
                *self.env['account.journal']._check_company_domain(self.company_id),
                ('type', 'in', ('bank', 'cash', 'credit'))], limit=1)
            if journal:
                method = journal._get_available_payment_method_lines(self.payment_type)[:1]
                defaults.update({'journal_id': journal.id, 'payment_method_line_id': method.id})
        return defaults
