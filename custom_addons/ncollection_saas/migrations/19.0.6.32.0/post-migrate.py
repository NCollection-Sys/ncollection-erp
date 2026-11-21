# -*- coding: utf-8 -*-
"""Feature parity: the accounting companions reach the tenants that ALREADY exist.

``ncollection_subscription/models/companions.py`` now derives Bank Transactions & Matching, the journal operations
centre (the enhanced bank / cash journal cards), invoice layout & calculations and the invoice amount audit from
Accounting, exactly like cheque printing. A NEW tenant gets them at database creation (provisioning reads the one
list, and its module gate now refuses READY without them). An EXISTING tenant would not: nothing in its plan changed,
so no plan-save hook fires. That is the tenant the enhanced journal card was missing from.

Same entry points as 19.0.6.16.0, no new engine: ``_config_sync_enqueue()`` re-pushes the licence and
``_nc_enqueue_module_install()`` queues one deduplicated install job per ready tenant. Installing what is already
installed is a no-op, so the tenants that received these modules by hand in earlier releases are unaffected. Only
tenants whose entitlement includes Accounting and that are trial or active are queued; nothing is uninstalled, no plan
is edited.
"""
from odoo import SUPERUSER_ID, api

ACCOUNTING_COMPANIONS = (
    'ncollection_bank_reconciliation',
    'ncollection_journal_operations',
    'ncollection_account_invoice_layout',
    'ncollection_account_audit',
)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    tenants = env['ncollection.tenant'].search([
        ('database_status', '=', 'ready'),
        ('database_name', '!=', False),
        # a suspended or expired workspace is not changed behind its owner's back; it gets them on reactivation
        # through the ordinary entitlement push
        ('status', 'in', ('trial', 'active')),
    ])
    gaining = tenants.filtered(
        lambda t: any(name in ACCOUNTING_COMPANIONS for name in t._nc_effective_module_list()))
    if not gaining:
        return
    gaining._config_sync_enqueue()
    gaining._nc_enqueue_module_install()
