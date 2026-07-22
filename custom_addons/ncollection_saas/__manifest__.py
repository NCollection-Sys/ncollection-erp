# pylint: disable=manifest-required-author
# (C8101 wants the OCA as author; this is a proprietary NCollection module.)
{
    'name': 'NCollection SaaS Admin',
    'version': '19.0.5.0.0',
    'category': 'Services/SaaS',
    'summary': 'SaaS provisioning + config sync + subscription billing (P2-T01/T02/T03/T11)',
    'author': 'NCollection',
    'website': 'https://ncollection.com',
    'license': 'LGPL-3',
    # ncollection_subscription: owns the tenant/plan/provisioning.job models we
    #   drive. ncollection_branding: installed into every tenant DB. queue_job:
    #   OCA async runner (repos.yml, pinned) that runs provisioning OFF the HTTP
    #   workers — the isolation guarantee (ARCHITECTURE_DATA_PLATFORM §10).
    # account + l10n_ae (P2-T11): billing invoices tenants for their
    # subscriptions in the ADMIN DB. l10n_ae supplies the UAE chart, AED and the
    # 5% VAT tax. Both are Odoo core modules (no new pinned/OCA dependency).
    'depends': ['ncollection_subscription', 'ncollection_branding', 'queue_job',
                'account', 'l10n_ae'],
    'data': [
        'data/provisioning_data.xml',
        'data/config_sync_data.xml',
        'views/provisioning_job_views.xml',
        'views/subscription_views.xml',
    ],
    'post_init_hook': 'post_init_hook',
}
