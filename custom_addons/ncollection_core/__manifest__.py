{
    'name': 'NCollection Core',
    # 1.10.0 (#221): tenant-side config-sync credential installer
    # (ncollection.config.sync.key) — THE single definition of the apikey write,
    # shared by the provisioning seed and the re-key job.
    'version': '19.0.1.10.0',
    'category': 'Hidden',
    'summary': 'Core access rights and security for NCollection ERP',
    'author': 'NCollection',
    'website': 'https://ncollection.com',
    'license': 'LGPL-3',
    # ncollection_branding (P1-T16): the Workspace Appearance page edits the
    # res.company nc_* branding fields defined by ncollection_branding. Per the
    # DELIVERABLE_1 §2.5 matrix branding ships in every tenant DB where core
    # lives, so the edge is core -> branding (branding stays installable
    # standalone in the admin DB, so the reverse would break its admin install).
    'depends': ['base', 'web', 'ncollection_branding'],
    'data': [
        'security/role_groups.xml',
        'security/config_sync_security.xml',
        'security/ir.model.access.csv',
        'views/workspace_settings_views.xml',
        'views/workspace_appearance_views.xml',
        'views/subscription_blocked_templates.xml',
        'views/dashboard_action.xml',
        'data/kpi_data.xml',
    ],
    # P1-T17 customer dashboard. NOTE the deliberate absence of new entries in
    # 'depends': the dashboard reads sale/account/crm through SOFT checks
    # (see models/dashboard/dashboard_data.py), so it never forces those apps
    # into a tenant's module set and leaves P1-T09/T10 licensing untouched.
    'assets': {
        'web.assets_backend': [
            # Chart.js ships with Odoo (web/static/lib/Chart/Chart.js) but is
            # not in the backend bundle by default. Listing it is idempotent —
            # Odoo de-duplicates asset paths — and avoids a runtime dependency
            # on some other module happening to pull it in first.
            'web/static/lib/Chart/Chart.js',
            'ncollection_core/static/src/dashboard/dashboard.scss',
            'ncollection_core/static/src/dashboard/dashboard.js',
            'ncollection_core/static/src/dashboard/dashboard.xml',
        ],
    },
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'auto_install': False,
}
