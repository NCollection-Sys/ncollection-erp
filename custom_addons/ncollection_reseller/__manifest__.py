{
    'name': 'NCollection White-Label Reseller System',
    'version': '19.0.1.0.0',
    'category': 'NCollection/Platform',
    'summary': 'Partner reseller accounts: cascading branding, sub-tenant '
               'management, provisioning quotas, and revenue-share reporting',
    'author': 'NCollection',
    'website': 'https://ncollection.com',
    'license': 'LGPL-3',
    'depends': ['ncollection_saas', 'ncollection_billing', 'ncollection_branding'],
    'data': [
        'security/reseller_groups.xml',
        'security/reseller_security.xml',
        'security/ir.model.access.csv',
        'views/reseller_views.xml',
        'views/revenue_share_views.xml',
        'views/reseller_menus.xml',
    ],
}
