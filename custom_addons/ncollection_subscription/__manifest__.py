{
    'name': 'NCollection Subscription Management',
    'version': '19.0.1.5.0',
    'category': 'Services/SaaS',
    'summary': 'SaaS subscription, tenant and plan management for NCollection ERP',
    'description': """
NCollection Subscription Management
====================================
SaaS management platform for NCollection ERP administrators:

* Subscription Plans
* Tenant Companies
* Subscriptions
* Trial account tracking
* KPI dashboard
""",
    'author': 'NCollection',
    'website': 'https://ncollection.com',
    'license': 'LGPL-3',
    'depends': ['base', 'mail'],
    'data': [
        'security/saas_security.xml',
        'security/ir.model.access.csv',
        'data/mail_data.xml',
        'views/subscription_plan_views.xml',
        'views/tenant_views.xml',
        'views/subscription_views.xml',
        'views/provisioning_job_views.xml',
        'views/dashboard_views.xml',
        'views/menus.xml',
        'data/demo_data.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'ncollection_subscription/static/src/scss/dashboard.scss',
            # #457 plan module picker (replaces typing technical module names).
            'ncollection_subscription/static/src/module_picker/module_picker.scss',
            'ncollection_subscription/static/src/module_picker/module_picker.js',
            'ncollection_subscription/static/src/module_picker/module_picker.xml',
        ],
    },
    'images': ['static/description/icon.png'],
    'installable': True,
    'application': True,
    'auto_install': False,
}
