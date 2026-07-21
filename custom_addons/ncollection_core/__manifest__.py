{
    'name': 'NCollection Core',
    'version': '19.0.1.7.0',
    'category': 'Hidden',
    'summary': 'Core access rights and security for NCollection ERP',
    'author': 'NCollection',
    'website': 'https://ncollection.com',
    'license': 'LGPL-3',
    'depends': ['base', 'web'],
    'data': [
        'security/role_groups.xml',
        'security/ir.model.access.csv',
        'views/workspace_settings_views.xml',
        'views/dashboard_action.xml',
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
