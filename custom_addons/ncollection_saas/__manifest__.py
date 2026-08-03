# pylint: disable=manifest-required-author
# (C8101 wants the OCA as author; this is a proprietary NCollection module.)
{
    'name': 'NCollection SaaS Admin',
    # 6.4.0 (#244): 'Restore in place' action on a failed fleet-migration line —
    # new method + a button in the line list, so `-u` applies the view change.
    # Minor bump: #283 adds two real columns to ncollection.tenant
    # (cron_report_miss_count, cron_report_activity_id), so `-u` is what
    # applies it. Same convention as #218's new-column bump.
    # Patch bump: #275 adds a guard inside ncollection.backup.restore_to.
    # No schema change -- pure validation -- but a real behaviour change on the
    # platform's most destructive primitive, so it gets traceability like #243's.
    'version': '19.0.6.5.2',
    'category': 'Services/SaaS',
    'summary': 'SaaS provisioning + auto-provisioning + config sync + fleet migration '
               '(P2-T01/T02/T03, P3-T14)',
    'author': 'NCollection',
    'website': 'https://ncollection.com',
    'license': 'LGPL-3',
    # ncollection_subscription: owns the tenant/plan/provisioning.job models we
    #   drive. ncollection_branding: installed into every tenant DB. queue_job:
    #   OCA async runner (repos.yml, pinned) that runs provisioning OFF the HTTP
    #   workers — the isolation guarantee (ARCHITECTURE_DATA_PLATFORM §10).
    'depends': ['ncollection_subscription', 'ncollection_branding', 'queue_job'],
    'data': [
        'security/ir.model.access.csv',
        'data/provisioning_data.xml',
        'data/config_sync_data.xml',
        'data/checkout_data.xml',
        'data/domain_data.xml',
        'data/backup_data.xml',
        'data/fleet_migration_data.xml',
        'views/provisioning_job_views.xml',
        'views/subscription_views.xml',
        'views/config_sync_views.xml',
        # after config_sync_views: inherits view_tenant_form_config_sync
        'views/config_sync_rekey_views.xml',
        'views/checkout_templates.xml',
        'views/domain_views.xml',
        'views/backup_views.xml',
        'views/fleet_migration_views.xml',
    ],
    'assets': {
        # Public checkout pages render via web.frontend_layout; ship their
        # behaviour + styling on the frontend bundle (tokens.scss loads there
        # too, from ncollection_branding).
        'web.assets_frontend': [
            'ncollection_saas/static/src/checkout/checkout.scss',
            'ncollection_saas/static/src/checkout/checkout.js',
        ],
    },
}
