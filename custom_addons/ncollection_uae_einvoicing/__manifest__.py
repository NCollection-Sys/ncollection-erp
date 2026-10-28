# pylint: disable=manifest-required-author
# (C8101 wants the OCA as author; this is a proprietary NCollection module.)
{
    'name': 'NCollection UAE E-Invoicing',
    'version': '19.0.1.0.0',
    'category': 'Accounting/Localizations',
    'summary': 'UAE e-invoicing engine (PINT-AE): reconciliation, XML generation, validation boundary, status model, '
    'audit and archive for customer invoices and credit notes. ASP-agnostic: no provider is built in.',
    'author': 'NCollection',
    'website': 'https://ncollection.com',
    'license': 'LGPL-3',
    # NOT an app, NOT in a plan, NOT a companion. It is a GATED module
    # (ncollection_subscription/models/gated_modules.py): eligible only for a tenant whose country is the UAE AND whose
    # books are verified as UAE books AND that already has Accounting, and activated only by an explicit per-tenant
    # `enable` override. Nothing installs it automatically.
    # account                              : account.move is the only accounting source of truth; the engine reads it
    # and never writes to it. ncollection_core                     : tenant-side licence machinery and role groups.
    # ncollection_account_core             : the shared financial base. ncollection_account_localization_uae : the
    # master data this engine consumes (nc_trn, nc_tin, legal registration, product classification) and the UAE chart.
    # NO queue_job dependency in this phase: nothing is submitted anywhere. Installing OCA queue_job into tenant
    # databases changes the tenant runtime topology, so it is decided together with the first ASP adapter, not before.
    # NO pip dependency and NO XSLT 2.0 processor: the official Schematron is run by an isolated validator worker
    # (satellites/einvoice_validator), never inside the Odoo image.
    'depends': ['account', 'ncollection_core', 'ncollection_account_core', 'ncollection_account_localization_uae'],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/sequence.xml',
    ],
}
