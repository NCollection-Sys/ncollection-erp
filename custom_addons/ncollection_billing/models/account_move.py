# -*- coding: utf-8 -*-
"""Link subscription invoices back to the tenant/subscription (P2-T11).

These invoices live in the ADMIN DB (NCollection billing its tenant customers)
and reference PLATFORM models only — never a tenant ERP DB (two-layer rule).
"""
from odoo import fields, models


class AccountMove(models.Model):
    _inherit = 'account.move'

    nc_subscription_id = fields.Many2one(
        'ncollection.subscription', string='NC Subscription',
        index=True, copy=False, ondelete='set null')
    nc_tenant_id = fields.Many2one(
        'ncollection.tenant', string='NC Tenant',
        index=True, copy=False, ondelete='set null')
    # The billed period — also the idempotency key ("exactly one per period").
    nc_period_start = fields.Date(string='NC Period Start', copy=False)
    nc_period_end = fields.Date(string='NC Period End', copy=False)

    # DB-level idempotency: a subscription can hold at most one invoice per
    # period_start. This is what actually guarantees "exactly one invoice per
    # period" under concurrent/duplicate lifecycle calls — the in-Python check
    # in subscription._nc_bill_period is the fast path, this is the backstop.
    # Postgres treats NULLs as distinct, so ordinary non-subscription invoices
    # (both columns NULL) never collide.
    _sql_constraints = [
        (
            'nc_billing_period_uniq',
            'unique(nc_subscription_id, nc_period_start)',
            'A subscription can only be invoiced once per billing period '
            '(NCollection billing idempotency).',
        ),
    ]
