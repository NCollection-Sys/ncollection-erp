# pylint: disable=manifest-required-author
# (C8101 wants the OCA as author; this is a proprietary NCollection module.)
{
    'name': 'NCollection SaaS Admin',
    'version': '19.0.4.0.0',
    'category': 'Services/SaaS',
    'summary': 'SaaS provisioning + auto-provisioning + config sync (P2-T01/T02/T03)',
    'author': 'NCollection',
    'website': 'https://ncollection.com',
    'license': 'LGPL-3',
    # ncollection_subscription: owns the tenant/plan/provisioning.job models we
    #   drive. ncollection_branding: installed into every tenant DB. queue_job:
    #   OCA async runner (repos.yml, pinned) that runs provisioning OFF the HTTP
    #   workers — the isolation guarantee (ARCHITECTURE_DATA_PLATFORM §10).
    'depends': ['ncollection_subscription', 'ncollection_branding', 'queue_job'],
    'data': [
        'data/provisioning_data.xml',
        'data/config_sync_data.xml',
        'views/provisioning_job_views.xml',
        'views/subscription_views.xml',
    ],
}
