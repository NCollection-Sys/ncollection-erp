# -*- coding: utf-8 -*-
"""Subscription-billing link on account.move (P2-T11, admin DB).

These invoices are the PLATFORM billing its own tenants in the ADMIN DB — this
is not a tenant-DB ERP model (two-layer separation forbids reaching into a
*tenant* database, not the platform using account.move on its own company;
DELIVERABLE_1 §2.5 places billing in the admin DB). We add a back-link to the
subscription/tenant being billed and a period key that makes invoice creation
idempotent: a unique (subscription, period_key) index guarantees a lifecycle
event (activation / a given renewal / a given upgrade) can only ever produce one
invoice, which is exactly the P2-T11 acceptance criterion. NULLs are distinct in
Postgres, so ordinary non-subscription invoices (both columns NULL) never clash.
"""

from odoo import fields, models


class AccountMove(models.Model):
    _inherit = 'account.move'

    ncollection_subscription_id = fields.Many2one(
        'ncollection.subscription',
        string='NCollection Subscription',
        index=True,
        copy=False,
        ondelete='set null',
        help='Set when this invoice bills an NCollection SaaS subscription (admin DB).',
    )
    ncollection_tenant_id = fields.Many2one(
        'ncollection.tenant',
        string='NCollection Tenant',
        index=True,
        copy=False,
        ondelete='set null',
    )
    ncollection_period_key = fields.Char(
        string='NCollection Billing Period Key',
        copy=False,
        help='Stable key for the billed lifecycle event (covered-period end, or a '
             'proration marker). Unique per subscription — the idempotency guard.',
    )

    _sql_constraints = [
        (
            'ncollection_period_uniq',
            'unique(ncollection_subscription_id, ncollection_period_key)',
            'A subscription can only be invoiced once per billing period '
            '(NCollection billing idempotency).',
        ),
    ]
