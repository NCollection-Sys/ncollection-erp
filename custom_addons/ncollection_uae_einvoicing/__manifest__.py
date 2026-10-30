# pylint: disable=manifest-required-author
# (C8101 wants the OCA as author; this is a proprietary NCollection module.)
{
    'name': 'NCollection UAE E-Invoicing',
    'version': '19.0.1.3.0',
    'category': 'Accounting/Localizations',
    'summary': 'UAE e-invoicing engine (PINT-AE): reconciliation, XML generation, validation boundary, status model, '
    'audit and archive for customer invoices and credit notes, and a provider-independent ASP transport engine '
    '(queue, retries, callbacks, confirmations). No provider is built in.',
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
    # queue_job                            : every call that waits on an ASP runs as a queued job on the `einvoice`
    # channel, never in a cron thread (DESIGN_CRON_AND_QUEUE_TOPOLOGY.md §2). Decided 2026-10-28 with the transport
    # engine; the runner topology for tenant databases is in UAE_EINVOICING.md ("Transport").
    # NO pip dependency and NO XSLT 2.0 processor: the official Schematron is run by an isolated validator worker
    # (satellites/einvoice_validator), never inside the Odoo image.
    'depends': [
        'account',
        'ncollection_core',
        'ncollection_account_core',
        'ncollection_account_localization_uae',
        'queue_job',
    ],
    # post_load: scopes OCA's jobrunner to eligible tenant databases in the einvoice-runner container only
    # (runner_scope.py; a no-op unless NC_EINVOICE_RUNNER=1)
    'post_load': 'post_load',
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/sequence.xml',
        'data/transport.xml',
        'views/einvoice_views.xml',
        'views/transport_views.xml',
        'views/config_views.xml',
        'views/menus.xml',
        'views/inbound_views.xml',
        'views/self_billing_views.xml',
        'views/export_views.xml',
    ],
}
