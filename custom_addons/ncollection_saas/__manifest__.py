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
    # Patch bump: #287 makes the restore drill drop its scratch database.
    # Patch bump: #295 purges a deleted tenant's backup directory.
    # Minor bump: #299 turns backup.database_name from a stored RELATED field
    # into a snapshot, so `-u` is what re-defines the column.
    # Patch bump: #285 makes the config-sync read deadline enforceable per
    # recv via the BufferedReader's read1(). Behaviour only, no schema.
    # 19.0.6.7.0 (#308): ECB rate source on the admin DB + the capability-gated
    # rate keys on the config-sync payload. No tenant makes an outbound call.
    # Patch bump: #245 gates the three SaaS-admin menus on base.group_system to
    # match their ACLs. `-u` is what applies it: convert.py only writes
    # group_ids when the groups= attribute is PRESENT, so adding one does
    # upgrade cleanly (removing one would not — the old value would survive).
    # Patch bump: #310 makes _cron_refresh_ecb_rate ENQUEUE the outbound fetch
    # onto a dedicated root.outbound channel instead of performing it on the
    # cron thread, and moves the fetch body into a PRIVATE _refresh_ecb_rate.
    # Private matters: the fetch is the method's first statement, so a public
    # name would let any authenticated admin-DB user hold an HTTP worker open
    # for the full deadline via RPC, before any ACL check is reached.
    # Pure Python, no schema change — a restart is enough and `-u` is not
    # required — but it is a real behaviour change on the platform's only
    # outbound call, so it gets traceability like #275's and #243's.
    'version': '19.0.6.8.0',
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
        # #455: module + config-sync visibility on the tenant form.
        'views/tenant_views.xml',
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
