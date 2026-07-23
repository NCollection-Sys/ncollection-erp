# -*- coding: utf-8 -*-
"""Hook Stripe payment confirmation to the subscription (P2-T13, admin DB).

Odoo's payment framework (payment / account_payment / payment_stripe) owns the
whole flow: the hosted checkout, the signature-verified webhook, and posting +
reconciling the invoice so its payment_state becomes paid. We add ONE thing —
when a confirmed transaction is tied to a subscription invoice, extend the
subscription to cover the paid period (and reactivate a lapsed one). A failed
transaction feeds the dunning seam (P2-T14).

Idempotency is required (ARCHITECTURE_SECURITY §7: webhook replay / cron
re-run): a per-transaction flag guarantees the subscription is only advanced
once per payment, even though _post_process may run from both the return poll
and the post-process cron.
"""
import logging

from odoo import fields, models

_logger = logging.getLogger(__name__)


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    nc_renewal_processed = fields.Boolean(
        string='NC Subscription Advanced', copy=False,
        help='Set once this transaction has extended its subscription — the '
             'idempotency guard against webhook replay / post-process re-runs.')

    def _post_process(self):
        """After core posts + reconciles the invoice, react to the outcome."""
        res = super()._post_process()
        for tx in self:
            sub_invoices = tx.invoice_ids.filtered('nc_subscription_id')
            if not sub_invoices:
                continue
            if tx.state == 'done' and not tx.nc_renewal_processed:
                for invoice in sub_invoices:
                    invoice.nc_subscription_id._nc_apply_payment(invoice)
                tx.nc_renewal_processed = True
            elif tx.state in ('error', 'cancel'):
                sub_invoices.nc_subscription_id._nc_on_payment_failed(tx)
        return res
